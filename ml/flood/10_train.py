"""Обучение модели подтопления: отложенный год и растущее окно. ADR 0010.

## Протокол

- **Отложенная выборка — целый год**, с 2025-07-01 по 2026-06-30. Полгода не
  покрывают все сезоны, а подтопление сезонное.
- **Проверка — растущее окно.** Три проверочных года подряд: 2022/23, 2023/24,
  2024/25. Модель фолда учится на всём, что было до его года. Вариант
  оценивается средним по трём годам, поэтому каждый сезон виден трижды.
- **Число деревьев** итоговой модели — медиана лучших итераций по фолдам.
  Итоговая модель учится на всём до отложенного года без ранней остановки.
- **Порог заявки** — точка равной полноты с наивной планкой на прогнозах трёх
  проверочных лет, собранных вместе. На отложенном году порог только
  применяется.

## Правила, которые держит код

- Вариант пишется в `out/variants_cv.json`. Девятый вариант скрипт не примет.
- Режим `variant` отложенный год не читает.
- Отложенный год открывается режимом `final` один раз. Повтор требует
  `--reopen "причина"`, и причина пишется в замер.
- Признаки дня недели, нерабочего дня, праздника и часа в модель не идут:
  метка разрешает у будней 16 часов, у выходного 24 (ADR 0010).

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/flood/10_train.py variant base --groups pump,evt,obj,cx,cal
    .venv\\Scripts\\python.exe ml/flood/10_train.py final base
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import warnings

import duckdb
import joblib
import lightgbm as lgb
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
PANEL = OUT / "panel_hourly.parquet"
VARIANTS = OUT / "variants_cv.json"
METRICS = OUT / "metrics.json"
MODEL = OUT / "model.txt"

PROTOCOL = "expanding_window_3_years_holdout_1_year"
FOLDS = (
    ("2022-07-01", "2023-07-01"),
    ("2023-07-01", "2024-07-01"),
    ("2024-07-01", "2025-07-01"),
)
TEST = ("2025-07-01", "2026-07-01")
BUDGET = 8
SEEDS = (4217, 1301, 7717)
NUM_ROUNDS = 2000
EARLY_STOPPING = 100

BASE_PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
    "num_threads": 8,
    "deterministic": True,
}

GROUPS = {
    "pump": ("pump_", "unit_pumps"),
    "evt": ("evt_", "unit_flood_sensors", "unit_age_days"),
    "obj": ("obj_",),
    "cx": ("cx_",),
    "cal": ("cal_season", "cal_month", "cal_day_of_year"),
    "check": ("check_",),
    "weather": ("weather_",),
    "recent": ("recent_",),
}
FORBIDDEN = (
    "cal_day_of_week",
    "cal_is_day_off",
    "cal_is_holiday",
    "cal_day_off_chain",
    "cal_hour",
)
NOT_FEATURES = {"object_id", "gallery", "picket", "hour", "label", "naive"}


def columns_for(all_columns: list[str], groups: list[str]) -> list[str]:
    chosen = [
        name
        for name in all_columns
        if any(name.startswith(p) or name == p for g in groups for p in GROUPS[g])
    ]
    leaked = [name for name in chosen if name in FORBIDDEN]
    if leaked:
        raise ValueError(f"признаки дня недели в модель не идут: {leaked}")
    return chosen


def load(con, columns: list[str], lo: str | None, hi: str | None) -> dict:
    where = ["hour(hour) = 0"]
    if lo:
        where.append(f"hour >= TIMESTAMP '{lo}'")
    if hi:
        where.append(f"hour < TIMESTAMP '{hi}'")
    frame = con.execute(
        f"SELECT {', '.join(columns)}, label, naive, hour FROM panel "
        f"WHERE {' AND '.join(where)} ORDER BY object_id, gallery, picket, hour"
    ).df()
    return {
        "x": frame[columns].to_numpy(dtype=np.float32),
        "y": frame["label"].to_numpy(dtype=np.int8),
        "naive": frame["naive"].to_numpy(dtype=np.int8),
        "hours": frame["hour"].to_numpy(),
    }


def subset(data: dict, mask: np.ndarray) -> dict:
    return {key: value[mask] for key, value in data.items()}


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(-p, kind="stable")
    ys = y[order]
    tp = np.cumsum(ys)
    total = float(tp[-1]) if len(tp) else 0.0
    if total == 0:
        return 0.0
    return float(np.sum(tp / np.arange(1, len(ys) + 1) * ys) / total)


def rule_point(y: np.ndarray, flag: np.ndarray) -> dict:
    alerts, hits, positives = float(flag.sum()), float((y * flag).sum()), float(y.sum())
    return {
        "precision": round(hits / alerts, 6) if alerts else 0.0,
        "recall": round(hits / positives, 6) if positives else 0.0,
        "alerts": int(alerts),
        "hits": int(hits),
    }


def threshold_for_recall(y: np.ndarray, p: np.ndarray, recall: float) -> float:
    order = np.argsort(-p, kind="stable")
    hits = np.cumsum(y[order])
    reached = np.nonzero(hits / float(y.sum()) >= recall)[0]
    return float(p[order][reached[0]]) if len(reached) else float(p.min())


def threshold_for_alerts(p: np.ndarray, alerts: int) -> float:
    ordered = np.sort(p)[::-1]
    return float(ordered[min(max(alerts, 1), len(ordered)) - 1])


def params_for(config: dict, seed: int) -> dict:
    return {**BASE_PARAMS, **config.get("params", {}), "seed": seed}


def connect() -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute(
        f"CREATE VIEW panel AS SELECT * FROM read_parquet('{PANEL.as_posix()}')"
    )
    names = [r[0] for r in con.execute("DESCRIBE SELECT * FROM panel").fetchall()]
    return con, [c for c in names if c not in NOT_FEATURES]


def cross_validate(con, config: dict, all_columns: list[str]) -> dict:
    """Растущее окно по трём проверочным годам и трём семенам."""
    columns = columns_for(all_columns, config["groups"])
    history = load(con, columns, None, TEST[0])
    window = config.get("train_window_days", 0)
    folds, seed_scores, oof = [], {s: [] for s in SEEDS}, {s: [] for s in SEEDS}
    best_iters = []
    for start, end in FOLDS:
        lo = np.datetime64(start)
        train_mask = history["hours"] < lo
        if window:
            train_mask &= history["hours"] >= lo - np.timedelta64(window, "D")
        valid_mask = (history["hours"] >= lo) & (history["hours"] < np.datetime64(end))
        train, valid = subset(history, train_mask), subset(history, valid_mask)
        naive = rule_point(valid["y"], valid["naive"] == 1)
        fold = {
            "valid": f"{start}..{end}",
            "rows_train": len(train["y"]),
            "base": round(float(valid["y"].mean()), 6),
            "naive": naive,
            "runs": [],
        }
        for seed in SEEDS:
            booster = lgb.train(
                params_for(config, seed),
                lgb.Dataset(train["x"], label=train["y"], feature_name=columns),
                num_boost_round=NUM_ROUNDS,
                valid_sets=[
                    lgb.Dataset(valid["x"], label=valid["y"], feature_name=columns)
                ],
                callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
            )
            p = booster.predict(valid["x"])
            score = pr_auc(valid["y"], p)
            at = rule_point(
                valid["y"], p >= threshold_for_recall(valid["y"], p, naive["recall"])
            )
            fold["runs"].append(
                {
                    "seed": seed,
                    "trees": booster.best_iteration,
                    "pr_auc": round(score, 6),
                    "precision_at_naive_recall": at["precision"],
                }
            )
            seed_scores[seed].append(score)
            oof[seed].append((valid["y"], valid["naive"], p))
            if seed == SEEDS[0]:
                best_iters.append(booster.best_iteration)
        folds.append(fold)

    means = [float(np.mean(seed_scores[s])) for s in SEEDS]
    pooled = []
    for seed in SEEDS:
        y = np.concatenate([a for a, _, _ in oof[seed]])
        nv = np.concatenate([b for _, b, _ in oof[seed]])
        p = np.concatenate([c for _, _, c in oof[seed]])
        naive = rule_point(y, nv == 1)
        threshold = threshold_for_recall(y, p, naive["recall"])
        pooled.append(
            {
                "seed": seed,
                "naive": naive,
                "threshold": threshold,
                "at_naive_recall": rule_point(y, p >= threshold),
            }
        )
    precision = float(np.mean([r["at_naive_recall"]["precision"] for r in pooled]))
    return {
        "columns": columns,
        "folds": folds,
        "pr_auc_mean": round(float(np.mean(means)), 6),
        "pr_auc_seed_spread": round(max(means) - min(means), 6),
        "pr_auc_by_fold": [
            round(float(np.mean([r["pr_auc"] for r in f["runs"]])), 6) for f in folds
        ],
        "oof_naive": pooled[0]["naive"],
        "oof_precision_at_naive_recall": round(precision, 6),
        "beats_naive_oof": precision > pooled[0]["naive"]["precision"],
        "best_iterations": best_iters,
        "_oof_seed0": oof[SEEDS[0]],
        "_threshold_seed0": pooled[0]["threshold"],
    }


def read_log() -> list[dict]:
    return json.loads(VARIANTS.read_text(encoding="utf-8")) if VARIANTS.exists() else []


def print_stop_rule(log: list[dict]) -> None:
    best, misses = None, 0
    for item in log:
        if item.get("kind") == "measure":
            continue
        gain = None if best is None else item["pr_auc_mean"] - best["pr_auc_mean"]
        if best is None or gain > max(
            best["pr_auc_seed_spread"], item["pr_auc_seed_spread"]
        ):
            best, misses = item, 0
        else:
            misses += 1
    if best:
        print(f"  лучший вариант {best['name']}: PR-AUC {best['pr_auc_mean']}")
    if misses >= 2:
        print("  СТОП: два варианта подряд без прироста больше разброса по семенам")


def cmd_variant(args: argparse.Namespace) -> None:
    log = read_log()
    if any(item["name"] == args.name for item in log):
        raise SystemExit(f"вариант {args.name} уже записан")
    if len(log) >= BUDGET:
        raise SystemExit(f"бюджет исчерпан: {len(log)} из {BUDGET}")
    con, all_columns = connect()
    groups = args.groups.split(",")
    for g in groups:
        if g not in GROUPS:
            raise SystemExit(f"неизвестная группа {g}")
    config = {
        "groups": groups,
        "train_window_days": args.train_window_days,
        "params": json.loads(args.params) if args.params else {},
    }
    t = time.time()
    scored = cross_validate(con, config, all_columns)
    entry = {
        "name": args.name,
        "kind": args.kind,
        "note": args.note,
        "config": config,
        **{k: v for k, v in scored.items() if not k.startswith("_")},
        "seconds": round(time.time() - t),
    }
    log.append(entry)
    VARIANTS.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"вариант {args.name} ({len(log)} из {BUDGET}), признаков {len(entry['columns'])}, "
        f"{entry['seconds']} c"
    )
    print(
        f"  PR-AUC по годам {entry['pr_auc_by_fold']}, среднее {entry['pr_auc_mean']}"
        f" ± {entry['pr_auc_seed_spread']}"
    )
    print(
        f"  планка по трём годам: точность {entry['oof_naive']['precision']}, "
        f"полнота {entry['oof_naive']['recall']}"
    )
    print(
        f"  модель при той же полноте: точность {entry['oof_precision_at_naive_recall']}"
        f", бьёт планку: {entry['beats_naive_oof']}"
    )
    print_stop_rule(log)


def cmd_final(args: argparse.Namespace) -> None:
    chosen = next((item for item in read_log() if item["name"] == args.name), None)
    if chosen is None:
        raise SystemExit(f"варианта {args.name} нет в журнале")
    previous = (
        json.loads(METRICS.read_text(encoding="utf-8")) if METRICS.exists() else {}
    )
    if previous.get("protocol") == PROTOCOL and not args.reopen:
        raise SystemExit(
            'отложенный год уже открыт. Повторно только ради ошибки: --reopen "причина"'
        )

    con, all_columns = connect()
    config = chosen["config"]
    scored = cross_validate(con, config, all_columns)
    columns = scored["columns"]
    rounds = int(np.median(scored["best_iterations"]))
    history = load(con, columns, None, TEST[0])
    if config.get("train_window_days"):
        keep = history["hours"] >= np.datetime64(TEST[0]) - np.timedelta64(
            config["train_window_days"], "D"
        )
        history = subset(history, keep)
    booster = lgb.train(
        params_for(config, SEEDS[0]),
        lgb.Dataset(history["x"], label=history["y"], feature_name=columns),
        num_boost_round=rounds,
    )

    # Порог и границы уровней с прогнозов трёх проверочных лет.
    oof = scored["_oof_seed0"]
    y_oof = np.concatenate([a for a, _, _ in oof])
    high = scored["_threshold_seed0"]
    medium = float(y_oof.mean())
    critical = next((c for c in (0.3, 0.4, 0.5, 0.6, 0.8) if c > high), high * 2)
    levels = {
        "MEDIUM": round(medium, 6),
        "HIGH": round(high, 6),
        "CRITICAL": round(critical, 6),
    }
    if [levels["MEDIUM"], levels["HIGH"], levels["CRITICAL"]] != sorted(
        levels.values()
    ):
        raise ValueError(f"границы уровней не возрастают: {levels}")

    test = load(con, columns, *TEST)
    p = booster.predict(test["x"])
    y = test["y"]
    naive = rule_point(y, test["naive"] == 1)
    at_threshold = rule_point(y, p >= high)
    equal_recall = rule_point(y, p >= threshold_for_recall(y, p, naive["recall"]))
    equal_alerts = rule_point(y, p >= threshold_for_alerts(p, naive["alerts"]))
    days = (
        int(
            (np.max(test["hours"]) - np.min(test["hours"]))
            .astype("timedelta64[D]")
            .astype(int)
        )
        + 1
    )

    # Сезоны отложенного года по отдельности: год нужен именно ради них.
    months = test["hours"].astype("datetime64[M]").astype(int) % 12 + 1
    seasons = {}
    for name, members in (
        ("зима", (12, 1, 2)),
        ("весна", (3, 4, 5)),
        ("лето", (6, 7, 8)),
        ("осень", (9, 10, 11)),
    ):
        mask = np.isin(months, members)
        ys, ps, ns = y[mask], p[mask], test["naive"][mask]
        nv = rule_point(ys, ns == 1)
        seasons[name] = {
            "base": round(float(ys.mean()), 6),
            "pr_auc": round(pr_auc(ys, ps), 6),
            "naive": nv,
            "at_threshold": rule_point(ys, ps >= high),
        }

    import shap

    sample = test["x"][:20000]
    explainer = shap.TreeExplainer(booster)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="LightGBM binary classifier")
        values = explainer.shap_values(sample)
    if isinstance(values, list):
        values = values[-1]
    prob = np.clip(booster.predict(sample), 1e-12, 1 - 1e-12)
    gap = float(
        np.max(
            np.abs(
                values.sum(axis=1)
                + explainer.expected_value
                - np.log(prob / (1 - prob))
            )
        )
    )
    if gap > 1e-6:
        raise ValueError(f"SHAP не аддитивен: расхождение {gap}")

    beats = bool(equal_recall["precision"] > naive["precision"])
    result = {
        "direction": "FLOOD_RISK",
        "protocol": PROTOCOL,
        "label": "ADR 0010: сигнал вне окна «будни 8:00–16:00»",
        "variant": chosen["name"],
        "config": config,
        "features": columns,
        "horizon_hours": 24,
        "reopened": args.reopen or None,
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "trees": rounds,
        "cv": {k: v for k, v in scored.items() if not k.startswith("_")},
        "test": {
            "period": f"{TEST[0]}..{TEST[1]}",
            "days": days,
            "rows": len(y),
            "base": round(float(y.mean()), 6),
            "pr_auc": round(pr_auc(y, p), 6),
            "naive": {**naive, "alerts_per_day": round(naive["alerts"] / days, 2)},
            "at_valid_threshold": {
                **at_threshold,
                "alerts_per_day": round(at_threshold["alerts"] / days, 2),
            },
            "at_naive_recall": equal_recall,
            "at_naive_alerts": equal_alerts,
            "beats_naive": beats,
            "lift_precision": round(equal_recall["precision"] / naive["precision"], 3)
            if naive["precision"]
            else None,
            "seasons": seasons,
        },
        "gain": {
            n: round(float(g), 1)
            for n, g in sorted(
                zip(columns, booster.feature_importance("gain"), strict=True),
                key=lambda kv: -kv[1],
            )
        },
        "shap": {
            "background": round(float(explainer.expected_value), 6),
            "max_additivity_gap": gap,
            "rows_checked": len(sample),
        },
        "decision": {
            "outcome": "model" if beats else "naive_rule",
            "threshold": levels["HIGH"],
            "levels": levels,
            "scale": "raw",
            "rule": "HIGH это порог равной полноты с планкой на прогнозах трёх проверочных лет",
            "chosen": {
                "cell_precision": at_threshold["precision"],
                "cell_recall": at_threshold["recall"],
                "alerts": at_threshold["alerts"],
                "alerts_per_day": round(at_threshold["alerts"] / days, 2),
            },
        },
        "targets": {
            "precision": naive["precision"],
            "recall": naive["recall"],
            "basis": "наивная планка «вода была вчера» на отложенном году",
        },
        "model_version": f"flood-{chosen['name']}-{time.strftime('%Y-%m-%d')}",
    }
    joblib.dump(booster, str(MODEL.with_suffix(".joblib")))
    booster.save_model(str(MODEL))
    METRICS.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8",
    )

    t = result["test"]
    print(
        f"итог {chosen['name']}: деревьев {rounds}, отложенный год {days} суток, база {t['base']}"
    )
    print(f"  PR-AUC {t['pr_auc']}")
    print(
        f"  планка: точность {naive['precision']}, полнота {naive['recall']}, тревог {naive['alerts']}"
    )
    print(
        f"  при той же полноте: точность {equal_recall['precision']} (в {t['lift_precision']} раза)"
    )
    print(f"  при том же числе тревог: полнота {equal_alerts['recall']}")
    print(
        f"  порог {levels['HIGH']}: точность {at_threshold['precision']}, "
        f"полнота {at_threshold['recall']}, тревог {at_threshold['alerts']}"
    )
    for name, s in seasons.items():
        print(
            f"  {name}: база {s['base']}, PR-AUC {s['pr_auc']}, планка {s['naive']['precision']}"
            f"/{s['naive']['recall']}, модель {s['at_threshold']['precision']}"
            f"/{s['at_threshold']['recall']}"
        )
    print(f"  бьёт планку: {beats}, SHAP {gap:.1e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("variant")
    v.add_argument("name")
    v.add_argument("--groups", required=True)
    v.add_argument("--train-window-days", type=int, default=0)
    v.add_argument("--params", default="", help="JSON поверх параметров LightGBM")
    v.add_argument(
        "--kind",
        choices=("search", "measure"),
        default="search",
        help="measure — замер вклада, в правило остановки не входит",
    )
    v.add_argument("--note", default="")
    f = sub.add_parser("final")
    f.add_argument("name")
    f.add_argument("--reopen", default="")
    args = parser.parse_args()
    {"variant": cmd_variant, "final": cmd_final}[args.cmd](args)


if __name__ == "__main__":
    main()
