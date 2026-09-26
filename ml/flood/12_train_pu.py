"""Модель подтопления на метке ADR 0012: вода, а не плановые проверки.

Протокол тот же, что в `10_train.py`: отложенный год с 2025-07-01, три
проверочных года растущим окном, три семени, бюджет восемь вариантов, признаки
дня недели запрещены. Отличий три, все из ADR 0012.

1. **Итоговая модель это среднее моделей фолдов** (`app.ml.ensemble`), а не
   модель, обученная заново на всех годах. Прогноз проверочного года даёт
   среднее семян его фолда, прогноз отложенного года даёт среднее всех моделей.
2. **Рабочий порог выбирается при равном числе тревог** с наивным правилом на
   прогнозах трёх проверочных лет. При равном числе тревог больше попаданий
   значит выше и precision, и recall.
3. **Модель принята, когда на отложенном году на рабочем пороге precision и
   recall оба выше, чем у наивного правила.**

Журнал вариантов: `out/variants_pu.json`.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/flood/12_train_pu.py variant base --groups pump,evt,obj,cx,cal
    .venv\\Scripts\\python.exe ml/flood/12_train_pu.py final base
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import pathlib
import sys
import time
import types

import joblib
import lightgbm as lgb
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
base = importlib.import_module("10_train")


def _load_ensemble_class() -> type:
    """Грузит `backend/app/ml/ensemble.py` под именем `app.ml.ensemble`.

    Сервис откроет сохранённую модель по этому имени. Пакет `app.ml` тянет
    sqlalchemy, которой в окружении обучения нет, а сам модуль ансамбля от
    бэкенда не зависит. Поэтому грузится один файл, а родительские пакеты
    ставятся пустыми.
    """
    path = HERE.parents[1] / "backend" / "app" / "ml" / "ensemble.py"
    for name in ("app", "app.ml"):
        sys.modules.setdefault(name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location("app.ml.ensemble", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["app.ml.ensemble"] = module
    spec.loader.exec_module(module)
    return module.MarginEnsemble


MarginEnsemble = _load_ensemble_class()

OUT = HERE / "out"
VARIANTS = OUT / "variants_pu.json"
METRICS = OUT / "metrics.json"
MODEL = OUT / "model.joblib"
PROTOCOL = "pu_label_fold_ensemble_equal_alerts_holdout_year"
BUDGET = 8


def sigmoid(margin: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-margin))


def cross_validate(con, config: dict, all_columns: list[str]) -> dict:
    columns = base.columns_for(all_columns, config["groups"])
    history = base.load(con, columns, None, base.TEST[0])
    folds, boosters, oof, oof_hours = [], [], [], []
    seed_scores = {s: [] for s in base.SEEDS}
    for start, end in base.FOLDS:
        lo = np.datetime64(start)
        train_mask = history["hours"] < lo
        valid_mask = (history["hours"] >= lo) & (history["hours"] < np.datetime64(end))
        train, valid = base.subset(history, train_mask), base.subset(history, valid_mask)
        margins = []
        for seed in base.SEEDS:
            booster = lgb.train(
                base.params_for(config, seed),
                lgb.Dataset(train["x"], label=train["y"], feature_name=columns),
                num_boost_round=base.NUM_ROUNDS,
                valid_sets=[lgb.Dataset(valid["x"], label=valid["y"], feature_name=columns)],
                callbacks=[lgb.early_stopping(base.EARLY_STOPPING, verbose=False)],
            )
            margin = booster.predict(valid["x"], raw_score=True)
            seed_scores[seed].append(base.pr_auc(valid["y"], margin))
            margins.append(margin)
            boosters.append(booster)
        p = sigmoid(np.mean(margins, axis=0))
        oof.append((valid["y"], valid["naive"], p))
        oof_hours.append(valid["hours"])
        naive = base.rule_point(valid["y"], valid["naive"] == 1)
        folds.append({
            "valid": f"{start}..{end}",
            "base": round(float(valid["y"].mean()), 6),
            "pr_auc": round(base.pr_auc(valid["y"], p), 6),
            "naive": naive,
        })

    y = np.concatenate([a for a, _, _ in oof])
    nv = np.concatenate([b for _, b, _ in oof])
    p = np.concatenate([c for _, _, c in oof])
    naive = base.rule_point(y, nv == 1)
    threshold = base.threshold_for_alerts(p, naive["alerts"])
    at = base.rule_point(y, p >= threshold)
    for fold, (fy, fn, fp) in zip(folds, oof, strict=True):
        fold["at_threshold"] = base.rule_point(fy, fp >= threshold)
    means = [float(np.mean(seed_scores[s])) for s in base.SEEDS]
    return {
        "columns": columns,
        "folds": folds,
        "pr_auc_mean": round(float(np.mean([f["pr_auc"] for f in folds])), 6),
        "pr_auc_seed_spread": round(max(means) - min(means), 6),
        "oof_naive": naive,
        "oof_threshold": round(threshold, 6),
        "oof_at_threshold": at,
        "beats_naive_both_oof": at["precision"] > naive["precision"]
        and at["recall"] > naive["recall"],
        "_boosters": boosters,
        "_oof_y": y,
        "_oof": oof,
        "_oof_hours": oof_hours,
    }


def read_log() -> list[dict]:
    return json.loads(VARIANTS.read_text(encoding="utf-8")) if VARIANTS.exists() else []


def cmd_variant(args: argparse.Namespace) -> None:
    log = read_log()
    if any(item["name"] == args.name for item in log):
        raise SystemExit(f"вариант {args.name} уже записан")
    if len(log) >= BUDGET:
        raise SystemExit(f"бюджет исчерпан: {len(log)} из {BUDGET}")
    groups = args.groups.split(",")
    for g in groups:
        if g not in base.GROUPS:
            raise SystemExit(f"неизвестная группа {g}")
    config = {"groups": groups, "params": json.loads(args.params) if args.params else {}}
    con, all_columns = base.connect()
    t = time.time()
    scored = cross_validate(con, config, all_columns)
    entry = {"name": args.name, "kind": args.kind, "note": args.note, "config": config,
             **{k: v for k, v in scored.items() if not k.startswith("_")},
             "seconds": round(time.time() - t)}
    log.append(entry)
    VARIANTS.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    nv, at = entry["oof_naive"], entry["oof_at_threshold"]
    print(f"вариант {args.name} ({len(log)} из {BUDGET}), признаков {len(entry['columns'])}")
    print(f"  PR-AUC по годам {[f['pr_auc'] for f in entry['folds']]}, среднее "
          f"{entry['pr_auc_mean']} ± {entry['pr_auc_seed_spread']}")
    print(f"  три проверочных года, тревог поровну ({nv['alerts']}):")
    print(f"    планка  precision {nv['precision']}, recall {nv['recall']}")
    print(f"    модель  precision {at['precision']}, recall {at['recall']}")
    for f in entry["folds"]:
        print(f"    {f['valid']}: планка {f['naive']['precision']}/{f['naive']['recall']}, "
              f"модель {f['at_threshold']['precision']}/{f['at_threshold']['recall']}")
    print(f"  бьёт планку по обоим: {entry['beats_naive_both_oof']}")
    base.print_stop_rule(log)


def cmd_final(args: argparse.Namespace) -> None:
    chosen = next((item for item in read_log() if item["name"] == args.name), None)
    if chosen is None:
        raise SystemExit(f"варианта {args.name} нет в журнале")
    previous = json.loads(METRICS.read_text(encoding="utf-8")) if METRICS.exists() else {}
    if previous.get("protocol") == PROTOCOL and not args.reopen:
        raise SystemExit('отложенный год уже открыт. Повторно только ради ошибки: --reopen "причина"')

    con, all_columns = base.connect()
    scored = cross_validate(con, chosen["config"], all_columns)
    columns = scored["columns"]
    model = MarginEnsemble(scored["_boosters"])
    threshold = scored["oof_threshold"]
    medium = float(scored["_oof_y"].mean())
    critical = next((c for c in (0.3, 0.4, 0.5, 0.6, 0.8) if c > threshold), threshold * 2)
    levels = {"MEDIUM": round(medium, 6), "HIGH": round(threshold, 6),
              "CRITICAL": round(critical, 6)}
    if [levels["MEDIUM"], levels["HIGH"], levels["CRITICAL"]] != sorted(levels.values()):
        raise ValueError(f"границы уровней не возрастают: {levels}")

    test = base.load(con, columns, *base.TEST)
    p = model.predict(test["x"])
    y = test["y"]
    naive = base.rule_point(y, test["naive"] == 1)
    at = base.rule_point(y, p >= threshold)
    days = int((np.max(test["hours"]) - np.min(test["hours"])).astype("timedelta64[D]").astype(int)) + 1
    months = test["hours"].astype("datetime64[M]").astype(int) % 12 + 1
    seasons = {}
    for name, members in (("зима", (12, 1, 2)), ("весна", (3, 4, 5)),
                          ("лето", (6, 7, 8)), ("осень", (9, 10, 11))):
        mask = np.isin(months, members)
        seasons[name] = {
            "base": round(float(y[mask].mean()), 6),
            "naive": base.rule_point(y[mask], test["naive"][mask] == 1),
            "model": base.rule_point(y[mask], p[mask] >= threshold),
        }

    sample = test["x"][:20000]
    values = model.shap_values(sample)
    gap = float(np.max(np.abs(values.sum(axis=1) + model.expected_value - model.margin(sample))))
    if gap > 1e-6:
        raise ValueError(f"SHAP ансамбля не аддитивен: расхождение {gap}")

    beats = bool(at["precision"] > naive["precision"] and at["recall"] > naive["recall"])
    gain = np.mean([b.feature_importance("gain") for b in model.boosters], axis=0)
    result = {
        "direction": "FLOOD_RISK",
        "protocol": PROTOCOL,
        "label": "ADR 0012: сутки пикета, признанные водой, а не плановой проверкой",
        "variant": chosen["name"],
        "config": chosen["config"],
        "features": columns,
        "models_in_ensemble": len(model.boosters),
        "horizon_hours": 24,
        "reopened": args.reopen or None,
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cv": {k: v for k, v in scored.items() if not k.startswith("_")},
        "test": {
            "period": f"{base.TEST[0]}..{base.TEST[1]}",
            "days": days,
            "rows": len(y),
            "base": round(float(y.mean()), 6),
            "pr_auc": round(base.pr_auc(y, p), 6),
            "naive": {**naive, "alerts_per_day": round(naive["alerts"] / days, 2)},
            "at_valid_threshold": {**at, "alerts_per_day": round(at["alerts"] / days, 2)},
            "beats_naive": beats,
            "seasons": seasons,
        },
        "gain": {n: round(float(g), 1) for n, g in sorted(
            zip(columns, gain, strict=True), key=lambda kv: -kv[1])},
        "shap": {"background": round(model.expected_value, 6),
                 "max_additivity_gap": gap, "rows_checked": len(sample)},
        "decision": {
            "outcome": "model" if beats else "naive_rule",
            "threshold": levels["HIGH"],
            "levels": levels,
            "scale": "raw",
            "rule": "HIGH даёт столько же тревог, сколько наивное правило, на трёх проверочных годах",
            "chosen": {"cell_precision": at["precision"], "cell_recall": at["recall"],
                       "alerts": at["alerts"], "alerts_per_day": round(at["alerts"] / days, 2)},
        },
        "targets": {"precision": naive["precision"], "recall": naive["recall"],
                    "basis": "наивная планка «вода была вчера» на отложенном году"},
        "model_version": f"flood-pu-{chosen['name']}-{time.strftime('%Y-%m-%d')}",
    }
    joblib.dump(model, str(MODEL))
    METRICS.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float),
                       encoding="utf-8")

    t = result["test"]
    print(f"итог {chosen['name']}: {len(model.boosters)} моделей, отложенный год {days} суток, "
          f"база {t['base']}, PR-AUC {t['pr_auc']}")
    print(f"  планка  тревог {naive['alerts']}, precision {naive['precision']}, recall {naive['recall']}")
    print(f"  модель  тревог {at['alerts']}, precision {at['precision']}, recall {at['recall']}")
    for name, s in seasons.items():
        print(f"  {name}: база {s['base']}, планка {s['naive']['precision']}/{s['naive']['recall']}, "
              f"модель {s['model']['precision']}/{s['model']['recall']}")
    print(f"  бьёт планку по обоим: {beats}, SHAP {gap:.1e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("variant")
    v.add_argument("name")
    v.add_argument("--groups", required=True)
    v.add_argument("--params", default="")
    v.add_argument("--kind", choices=("search", "measure"), default="search")
    v.add_argument("--note", default="")
    f = sub.add_parser("final")
    f.add_argument("name")
    f.add_argument("--reopen", default="")
    args = parser.parse_args()
    {"variant": cmd_variant, "final": cmd_final}[args.cmd](args)


if __name__ == "__main__":
    main()
