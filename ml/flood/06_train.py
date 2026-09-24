"""Обучение модели подтопления с бюджетом вариантов и одним открытием теста.

Скрипт держит правила цели в коде, а не в памяти исполнителя.

- **Вариант** это одно решение, проверенное на проверочном отрезке: сетка,
  набор групп признаков, окно обучения. Каждый вариант пишется в
  `out/variants.json`. Девятый вариант скрипт не примет.
- **Сравнение только на проверочном отрезке.** Режим `variant` отложенную
  выборку не читает вовсе.
- **Отложенная выборка открывается один раз**, режимом `final`. Повторное
  открытие требует флага `--reopen "причина"`, и причина пишется в замер.
- **Порог выбирается на проверочном отрезке**: точка равной полноты с наивной
  планкой. На отложенной выборке порог только применяется.

Все варианты меряются на строках 00:00, то есть на суточной сетке прогноза.
Модель часовой сетки учится на всех часах, но сравнивается на тех же строках,
что и суточная: иначе базы и число строк разные, и PR-AUC не сравнимы.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/flood/06_train.py variant v1 --grid day --groups all
    .venv\\Scripts\\python.exe ml/flood/06_train.py final v1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import duckdb
import joblib
import lightgbm as lgb
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
PANEL = OUT / "panel_hourly.parquet"
VARIANTS = OUT / "variants.json"
METRICS = OUT / "metrics.json"
MODEL = OUT / "model.txt"

TRAIN_END = "2025-07-01"
VALID_END = "2026-01-01"
BUDGET = 8
SEEDS = (4217, 1301, 7717)
NUM_ROUNDS = 2000
EARLY_STOPPING = 100

PARAMS = {
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

# Группы признаков в порядке цели. Признаки устройства места идут с группой,
# которой они нужны: число насосов с насосами, число датчиков с историей.
GROUPS = {
    "pump": ("pump_", "unit_pumps"),
    "evt": ("evt_", "unit_flood_sensors", "unit_age_days"),
    "obj": ("obj_",),
    "cx": ("cx_",),
    "cal": ("cal_",),
}
NOT_FEATURES = {"object_id", "gallery", "picket", "hour", "label", "naive"}


def columns_for(all_columns: list[str], groups: list[str]) -> list[str]:
    chosen = []
    for name in all_columns:
        for group in groups:
            if any(name.startswith(p) or name == p for p in GROUPS[group]):
                chosen.append(name)
                break
    return chosen


def load(con, columns: list[str], lo: str | None, hi: str | None, daily: bool) -> dict:
    where = []
    if lo:
        where.append(f"hour >= TIMESTAMP '{lo}'")
    if hi:
        where.append(f"hour < TIMESTAMP '{hi}'")
    if daily:
        where.append("hour(hour) = 0")
    frame = con.execute(
        f"SELECT {', '.join(columns)}, label, naive, hour FROM panel "
        f"WHERE {' AND '.join(where) or 'TRUE'} ORDER BY object_id, gallery, picket, hour"
    ).df()
    return {
        "x": frame[columns].to_numpy(dtype=np.float32),
        "y": frame["label"].to_numpy(dtype=np.int8),
        "naive": frame["naive"].to_numpy(dtype=np.int8),
        "hours": frame["hour"].to_numpy(),
    }


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(-p, kind="stable")
    y_sorted = y[order]
    tp = np.cumsum(y_sorted)
    total = float(tp[-1]) if len(tp) else 0.0
    if total == 0:
        return 0.0
    precision = tp / np.arange(1, len(y_sorted) + 1)
    return float(np.sum(precision * y_sorted) / total)


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


def train(
    train_set: dict, valid_set: dict, columns: list[str], seed: int
) -> lgb.Booster:
    params = {**PARAMS, "seed": seed}
    return lgb.train(
        params,
        lgb.Dataset(train_set["x"], label=train_set["y"], feature_name=columns),
        num_boost_round=NUM_ROUNDS,
        valid_sets=[
            lgb.Dataset(valid_set["x"], label=valid_set["y"], feature_name=columns)
        ],
        callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
    )


def read_log() -> list[dict]:
    if not VARIANTS.exists():
        return []
    return json.loads(VARIANTS.read_text(encoding="utf-8"))


def connect() -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    con.execute(
        f"CREATE VIEW panel AS SELECT * FROM read_parquet('{PANEL.as_posix()}')"
    )
    names = [r[0] for r in con.execute("DESCRIBE SELECT * FROM panel").fetchall()]
    return con, [c for c in names if c not in NOT_FEATURES]


def fit_and_score(con, config: dict, all_columns: list[str]) -> dict:
    """Учит модель по трём семенам, меряет на строках 00:00 проверочного отрезка."""
    columns = columns_for(all_columns, config["groups"])
    daily = config["grid"] == "day"
    lo = None
    if config.get("train_window_days"):
        lo = str(
            np.datetime64(TRAIN_END) - np.timedelta64(config["train_window_days"], "D")
        )
    train_set = load(con, columns, lo, TRAIN_END, daily)
    # Ранняя остановка идёт по той же сетке, на которой учится модель.
    stop_set = load(con, columns, TRAIN_END, VALID_END, daily)
    valid_day = load(con, columns, TRAIN_END, VALID_END, True)

    naive = rule_point(valid_day["y"], valid_day["naive"] == 1)
    runs, boosters = [], []
    for seed in SEEDS:
        booster = train(train_set, stop_set, columns, seed)
        p = booster.predict(valid_day["x"])
        threshold = threshold_for_recall(valid_day["y"], p, naive["recall"])
        at_recall = rule_point(valid_day["y"], p >= threshold)
        runs.append(
            {
                "seed": seed,
                "trees": booster.num_trees(),
                "pr_auc_valid": round(pr_auc(valid_day["y"], p), 6),
                "precision_at_naive_recall": at_recall["precision"],
                "threshold_at_naive_recall": round(threshold, 6),
            }
        )
        boosters.append(booster)
    scores = [r["pr_auc_valid"] for r in runs]
    return {
        "columns": columns,
        "rows_train": len(train_set["y"]),
        "rows_valid_daily": len(valid_day["y"]),
        "base_valid_daily": round(float(valid_day["y"].mean()), 6),
        "naive_valid": naive,
        "runs": runs,
        "pr_auc_valid_mean": round(float(np.mean(scores)), 6),
        "pr_auc_valid_spread": round(float(max(scores) - min(scores)), 6),
        "precision_at_naive_recall_mean": round(
            float(np.mean([r["precision_at_naive_recall"] for r in runs])), 6
        ),
        "beats_naive_valid": bool(
            np.mean([r["precision_at_naive_recall"] for r in runs]) > naive["precision"]
        ),
        "_boosters": boosters,
        "_valid_day": valid_day,
    }


def cmd_variant(args: argparse.Namespace) -> None:
    log = read_log()
    if any(item["name"] == args.name for item in log):
        raise SystemExit(f"вариант {args.name} уже записан, имя обязано быть новым")
    if len(log) >= BUDGET:
        raise SystemExit(f"бюджет исчерпан: записано {len(log)} вариантов из {BUDGET}")
    con, all_columns = connect()
    groups = list(GROUPS) if args.groups == "all" else args.groups.split(",")
    for g in groups:
        if g.startswith("-"):
            continue
        if g not in GROUPS:
            raise SystemExit(f"неизвестная группа {g}")
    if any(g.startswith("-") for g in groups):
        drop = {g[1:] for g in groups if g.startswith("-")}
        groups = [g for g in GROUPS if g not in drop]
    config = {
        "grid": args.grid,
        "groups": groups,
        "train_window_days": args.train_window_days,
    }
    t = time.time()
    scored = fit_and_score(con, config, all_columns)
    entry = {
        "name": args.name,
        "note": args.note,
        "config": config,
        **{k: v for k, v in scored.items() if not k.startswith("_")},
        "seconds": round(time.time() - t),
    }
    log.append(entry)
    VARIANTS.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"вариант {args.name} ({len(log)} из {BUDGET}): {config}")
    print(f"  признаков {len(entry['columns'])}, строк обучения {entry['rows_train']}")
    print(
        f"  PR-AUC на проверке {entry['pr_auc_valid_mean']} ± {entry['pr_auc_valid_spread']}"
        f" (семена {[r['pr_auc_valid'] for r in entry['runs']]})"
    )
    print(
        f"  планка на проверке: точность {entry['naive_valid']['precision']}, "
        f"полнота {entry['naive_valid']['recall']}"
    )
    print(
        f"  модель при той же полноте: точность {entry['precision_at_naive_recall_mean']}"
        f", бьёт планку: {entry['beats_naive_valid']}"
    )
    print_stop_rule(log)


def print_stop_rule(log: list[dict]) -> None:
    """Правило остановки: два варианта подряд без прироста больше разброса."""
    best = None
    misses = 0
    for item in log:
        score = item["pr_auc_valid_mean"]
        if (
            best is None
            or score - best["pr_auc_valid_mean"] > best["pr_auc_valid_spread"]
        ):
            best, misses = item, 0
        else:
            misses += 1
    print(f"  лучший вариант {best['name']}: PR-AUC {best['pr_auc_valid_mean']}")
    if misses >= 2:
        print("  СТОП: два варианта подряд без прироста больше разброса по семенам")


def chosen_point(point: dict, days: int) -> dict:
    """Рабочая точка на пороге заявки. Её публикует `app/ml/publish.py`."""
    return {
        "cell_precision": point["precision"],
        "cell_recall": point["recall"],
        "alerts": point["alerts"],
        "alerts_per_day": round(point["alerts"] / days, 2),
    }


def targets_from(naive: dict) -> dict:
    """Цель по качеству направления: наивная планка на том же отрезке (ADR 0009)."""
    return {
        "precision": naive["precision"],
        "recall": naive["recall"],
        "basis": "наивная планка «событие было вчера» на отложенной выборке",
    }


def cmd_final(args: argparse.Namespace) -> None:
    log = read_log()
    chosen = next((item for item in log if item["name"] == args.name), None)
    if chosen is None:
        raise SystemExit(f"варианта {args.name} нет в журнале")
    previous = (
        json.loads(METRICS.read_text(encoding="utf-8")) if METRICS.exists() else None
    )
    if previous and not args.reopen:
        raise SystemExit(
            "отложенная выборка уже открыта. Повторно только ради ошибки в коде: "
            '--reopen "причина"'
        )

    con, all_columns = connect()
    scored = fit_and_score(con, chosen["config"], all_columns)
    columns = scored["columns"]
    booster = scored["_boosters"][0]
    valid_day = scored["_valid_day"]

    # Порог решается на проверочном отрезке и только применяется к тесту.
    p_valid = booster.predict(valid_day["x"])
    naive_valid = scored["naive_valid"]
    high = threshold_for_recall(valid_day["y"], p_valid, naive_valid["recall"])
    valid_at_high = rule_point(valid_day["y"], p_valid >= high)

    test = load(con, columns, VALID_END, None, True)
    p_test = booster.predict(test["x"])
    y = test["y"]
    naive = rule_point(y, test["naive"] == 1)
    at_valid_threshold = rule_point(y, p_test >= high)
    equal_recall = rule_point(
        y, p_test >= threshold_for_recall(y, p_test, naive["recall"])
    )
    equal_alerts = rule_point(
        y, p_test >= threshold_for_alerts(p_test, naive["alerts"])
    )
    days = (
        int(
            (np.max(test["hours"]) - np.min(test["hours"]))
            .astype("timedelta64[D]")
            .astype(int)
        )
        + 1
    )

    seeds = []
    for other in scored["_boosters"]:
        p_other = other.predict(test["x"])
        seeds.append(round(pr_auc(y, p_other), 6))

    import shap

    sample = test["x"][:20000]
    explainer = shap.TreeExplainer(booster)
    contributions = explainer.shap_values(sample)
    prob = np.clip(booster.predict(sample), 1e-12, 1 - 1e-12)
    gap = float(
        np.max(
            np.abs(
                contributions.sum(axis=1)
                + explainer.expected_value
                - np.log(prob / (1 - prob))
            )
        )
    )
    if gap > 1e-6:
        raise ValueError(f"SHAP не аддитивен: расхождение {gap}")

    medium = float(np.mean(np.concatenate([valid_day["y"]])))
    critical = next((c for c in (0.3, 0.4, 0.5, 0.6, 0.8) if c > high), high * 2)
    levels = {
        "MEDIUM": round(medium, 6),
        "HIGH": round(high, 6),
        "CRITICAL": round(critical, 6),
    }
    values = [levels["MEDIUM"], levels["HIGH"], levels["CRITICAL"]]
    if values != sorted(values) or len(set(values)) != 3:
        raise ValueError(f"границы уровней не возрастают: {levels}")

    beats = bool(equal_recall["precision"] > naive["precision"])
    result = {
        "direction": "FLOOD_RISK",
        "variant": chosen["name"],
        "config": chosen["config"],
        "features": columns,
        "horizon_hours": 24,
        "reopened": args.reopen or None,
        "previous_opening": previous.get("opened_at") if previous else None,
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "trees": booster.num_trees(),
        "valid": {
            "pr_auc": scored["runs"][0]["pr_auc_valid"],
            "naive": naive_valid,
            "at_threshold": valid_at_high,
        },
        "test": {
            "days": days,
            "rows": len(y),
            "base": round(float(y.mean()), 6),
            "pr_auc": round(pr_auc(y, p_test), 6),
            "pr_auc_seeds": seeds,
            "naive": {**naive, "alerts_per_day": round(naive["alerts"] / days, 2)},
            "at_valid_threshold": {
                **at_valid_threshold,
                "alerts_per_day": round(at_valid_threshold["alerts"] / days, 2),
            },
            "at_naive_recall": equal_recall,
            "at_naive_alerts": equal_alerts,
            "beats_naive": beats,
            "lift_precision": round(equal_recall["precision"] / naive["precision"], 3)
            if naive["precision"]
            else None,
        },
        "gain": {
            name: round(float(g), 1)
            for name, g in sorted(
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
            "rule": "HIGH это порог равной полноты с планкой на проверочном отрезке",
            "chosen": chosen_point(at_valid_threshold, days),
        },
        "targets": targets_from(naive),
    }
    joblib.dump(booster, str(MODEL.with_suffix(".joblib")))
    booster.save_model(str(MODEL))
    METRICS.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8",
    )

    t = result["test"]
    print(f"итоговый вариант {chosen['name']}, отложенная выборка {days} суток")
    print(f"  база {t['base']}, PR-AUC {t['pr_auc']} (семена {seeds})")
    print(
        f"  планка: точность {naive['precision']}, полнота {naive['recall']}, "
        f"тревог {naive['alerts']}"
    )
    print(
        f"  при той же полноте: точность {equal_recall['precision']} "
        f"(в {t['lift_precision']} раза)"
    )
    print(f"  при том же числе тревог: полнота {equal_alerts['recall']}")
    print(
        f"  порог с проверки {levels['HIGH']}: точность {at_valid_threshold['precision']}, "
        f"полнота {at_valid_threshold['recall']}"
    )
    print(f"  бьёт планку: {beats}, SHAP расхождение {gap:.2e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("variant")
    v.add_argument("name")
    v.add_argument("--grid", choices=("day", "hour"), required=True)
    v.add_argument(
        "--groups",
        required=True,
        help="all, список через запятую или all без групп: -pump",
    )
    v.add_argument("--train-window-days", type=int, default=0)
    v.add_argument("--note", default="")
    f = sub.add_parser("final")
    f.add_argument("name")
    f.add_argument("--reopen", default="")
    args = parser.parse_args()
    {"variant": cmd_variant, "final": cmd_final}[args.cmd](args)


if __name__ == "__main__":
    main()
