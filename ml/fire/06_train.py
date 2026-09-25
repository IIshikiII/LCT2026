"""Модель пожарного риска. ADR 0014, протокол ADR 0011 и ADR 0012.

Протокол берётся у подтопления без изменений (`ml/flood/10_train.py`,
`ml/flood/12_train_pu.py`). Здесь меняются только панель, группы признаков и
пути журналов.

- Отложенный год с 2025-07-01 по 2026-06-30.
- Три проверочных года растущим окном, три семени, ранняя остановка.
- Бюджет восемь вариантов, журнал `out/variants.json`.
- Итоговая модель это среднее моделей фолдов (`app.ml.ensemble`).

Порядок записан до первого запуска и не меняется по результату.

1. `variant`: варианты сравниваются по средней PR-AUC трёх проверочных лет.
2. `budget`: для лучшего варианта выбирается окно скользящего бюджета тревог
   (7, 30 или 90 суток) по числу попаданий трёх проверочных лет вместе.
   Отложенный год команда не читает.
3. `final`: отложенный год открывается один раз. Тревоги ставит скользящий
   бюджет выбранного окна от правила «событие было вчера».
4. **Приёмка:** модель со скользящим бюджетом выше правила и по precision, и по
   recall на каждом из трёх проверочных лет. Иначе модели в сервисе нет, и
   направление работает на правиле (ADR 0015).

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/fire/06_train.py variant base --groups evt,chk,obj,cal
    .venv\\Scripts\\python.exe ml/fire/06_train.py budget base
    .venv\\Scripts\\python.exe ml/fire/06_train.py final base
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
import time

import joblib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
FLOOD = HERE.parents[0] / "flood"
sys.path.insert(0, str(FLOOD))

base = importlib.import_module("10_train")
train = importlib.import_module("12_train_pu")
rolling = importlib.import_module("14_rolling_budget")

OUT = HERE / "out"
base.PANEL = OUT / "panel_daily.parquet"
base.GROUPS = {
    "evt": ("evt_", "unit_"),
    "line": ("line_",),
    "chk": ("chk_",),
    "obj": ("obj_",),
    "tmp": ("tmp_",),
    "gas": ("gas_",),
    "hw": ("hw_",),
    "pwr": ("pwr_",),
    "ppl": ("ppl_",),
    "wx": ("wx_",),
    "cal": ("cal_season", "cal_month", "cal_day_of_year", "cal_fireworks"),
    "rec": ("rec_",),
    "cond": ("cond_",),
    "geo": ("geo_",),
    "week": ("week_",),
}
# Журнал `variants.json` держит четыре варианта на метке «вне окна будни 8–16».
# Метка сменилась на «сигнал вне пачки» (ADR 0014, пересмотр), поэтому варианты
# новой метки идут своим журналом со своим бюджетом. Отложенный год не открывался.
train.VARIANTS = OUT / "variants_v2.json"
train.METRICS = OUT / "metrics.json"
train.MODEL = OUT / "model.joblib"
PROTOCOL = "fire_burst_label_fold_ensemble_rolling_budget_holdout_year"
ROLLING = OUT / "rolling_budget_v2.json"
WINDOWS = (7, 30, 90)


def cmd_budget(args: argparse.Namespace) -> None:
    chosen = next((i for i in train.read_log() if i["name"] == args.name), None)
    if chosen is None:
        raise SystemExit(f"варианта {args.name} нет в журнале")
    con, all_columns = base.connect()
    scored = train.cross_validate(con, chosen["config"], all_columns)
    result: dict = {"variant": args.name, "holdout_read": False, "windows": {}}
    for window in WINDOWS:
        folds, ys, nvs, flags = [], [], [], []
        for (y, nv, p), hours, meta in zip(
            scored["_oof"], scored["_oof_hours"], scored["folds"], strict=True
        ):
            flag = rolling.rolling_alerts(p, nv, hours.astype("datetime64[D]"), window)
            naive, model = base.rule_point(y, nv == 1), base.rule_point(y, flag)
            folds.append({
                "valid": meta["valid"], "naive": naive, "model": model,
                "beats_both": model["precision"] > naive["precision"]
                and model["recall"] > naive["recall"],
            })
            ys.append(y), nvs.append(nv), flags.append(flag)
        y, nv, flag = np.concatenate(ys), np.concatenate(nvs), np.concatenate(flags)
        pooled = {"naive": base.rule_point(y, nv == 1), "model": base.rule_point(y, flag)}
        result["windows"][str(window)] = {
            "folds": folds, "pooled": pooled,
            "beats_every_year": all(f["beats_both"] for f in folds),
        }
        print(f"окно {window} суток")
        for f in folds + [{"valid": "три года вместе", **pooled, "beats_both": None}]:
            n, m = f["naive"], f["model"]
            print(f"  {f['valid']}: тревог {n['alerts']}/{m['alerts']}, "
                  f"попаданий {n['hits']}/{m['hits']}, правило {n['precision']:.3f}/"
                  f"{n['recall']:.3f}, модель {m['precision']:.3f}/{m['recall']:.3f}")
    best = max(result["windows"], key=lambda w: result["windows"][w]["pooled"]["model"]["hits"])
    result["chosen_window"] = int(best)
    result["accepted"] = result["windows"][best]["beats_every_year"]
    ROLLING.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"выбрано окно {best} суток, модель выше правила на каждом годе: {result['accepted']}")


def cmd_final(args: argparse.Namespace) -> None:
    chosen = next((i for i in train.read_log() if i["name"] == args.name), None)
    if chosen is None:
        raise SystemExit(f"варианта {args.name} нет в журнале")
    budget = json.loads(ROLLING.read_text(encoding="utf-8"))
    if budget["variant"] != args.name:
        raise SystemExit(f"окно бюджета выбрано для {budget['variant']}, а не для {args.name}")
    previous = json.loads(train.METRICS.read_text(encoding="utf-8")) if train.METRICS.exists() else {}
    if previous.get("protocol") == PROTOCOL and not args.reopen:
        raise SystemExit('отложенный год уже открыт. Повторно только ради ошибки: --reopen "причина"')

    window = int(budget["chosen_window"])
    con, all_columns = base.connect()
    scored = train.cross_validate(con, chosen["config"], all_columns)
    columns = scored["columns"]
    model = train.MarginEnsemble(scored["_boosters"])
    threshold = scored["oof_threshold"]
    medium = float(scored["_oof_y"].mean())
    critical = next((c for c in (0.05, 0.1, 0.2, 0.3, 0.5) if c > threshold), threshold * 2)
    levels = {"MEDIUM": round(medium, 6), "HIGH": round(threshold, 6),
              "CRITICAL": round(critical, 6)}
    if [levels["MEDIUM"], levels["HIGH"], levels["CRITICAL"]] != sorted(levels.values()):
        raise ValueError(f"границы уровней не возрастают: {levels}")

    # Первые сутки окна бюджета берутся из прогнозов перед отложенным годом:
    # это прошлое, известное утром.
    start = str(np.datetime64(base.TEST[0]) - np.timedelta64(window - 1, "D"))
    data = base.load(con, columns, start, base.TEST[1])
    p_all = model.predict(data["x"])
    days_all = data["hours"].astype("datetime64[D]")
    flag_all = rolling.rolling_alerts(p_all, data["naive"], days_all, window)
    test = days_all >= np.datetime64(base.TEST[0])
    y, nv, p, flag = data["y"][test], data["naive"][test], p_all[test], flag_all[test]
    days = days_all[test]
    n_days = int((days.max() - days.min()).astype(int)) + 1
    naive = base.rule_point(y, nv == 1)
    at = base.rule_point(y, flag)
    fixed = base.rule_point(y, p >= threshold)
    months = days.astype("datetime64[M]").astype(int) % 12 + 1
    seasons = {}
    for name, members in (("зима", (12, 1, 2)), ("весна", (3, 4, 5)),
                          ("лето", (6, 7, 8)), ("осень", (9, 10, 11))):
        m = np.isin(months, members)
        seasons[name] = {"base": round(float(y[m].mean()), 6),
                         "naive": base.rule_point(y[m], nv[m] == 1),
                         "model": base.rule_point(y[m], flag[m])}

    sample = data["x"][test][:20000]
    gap = float(np.max(np.abs(model.shap_values(sample).sum(axis=1)
                              + model.expected_value - model.margin(sample))))
    if gap > 1e-6:
        raise ValueError(f"SHAP ансамбля не аддитивен: расхождение {gap}")

    beats = bool(at["precision"] > naive["precision"] and at["recall"] > naive["recall"])
    gain = np.mean([b.feature_importance("gain") for b in model.boosters], axis=0)
    result = {
        "direction": "FIRE_RISK",
        "protocol": PROTOCOL,
        "label": "ADR 0014: сигнал дыма, «выше 40ºC» или теплового датчика вне пачки обхода или сбоя",
        "variant": chosen["name"],
        "config": chosen["config"],
        "features": columns,
        "models_in_ensemble": len(model.boosters),
        "horizon_hours": 24,
        "reopened": args.reopen or None,
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cv": {k: v for k, v in scored.items() if not k.startswith("_")},
        "rolling_budget_cv": budget,
        "test": {
            "period": f"{base.TEST[0]}..{base.TEST[1]}",
            "days": n_days,
            "rows": len(y),
            "base": round(float(y.mean()), 6),
            "pr_auc": round(base.pr_auc(y, p), 6),
            "naive": {**naive, "alerts_per_day": round(naive["alerts"] / n_days, 2)},
            "at_budget": {**at, "alerts_per_day": round(at["alerts"] / n_days, 2)},
            "at_valid_threshold": {**fixed, "alerts_per_day": round(fixed["alerts"] / n_days, 2)},
            "beats_naive": beats,
            "seasons": seasons,
        },
        "gain": {n: round(float(g), 1) for n, g in sorted(
            zip(columns, gain, strict=True), key=lambda kv: -kv[1])},
        "shap": {"background": round(model.expected_value, 6),
                 "max_additivity_gap": gap, "rows_checked": len(sample)},
        "decision": {
            "outcome": "rolling_budget" if budget["accepted"] else "naive_rule",
            "threshold": levels["HIGH"],
            "levels": levels,
            "scale": "raw",
            "rule": (f"скользящий бюджет: за последние {window} суток тревог столько же, "
                     "сколько у правила «событие было вчера»; HIGH это порог, который держит бюджет"),
            "alert_budget": {
                "window_days": window,
                "baseline": "правило «событие было вчера»",
                "fallback_threshold": levels["HIGH"],
                "fallback_note": "порог с проверочных лет, пока истории прогнозов нет",
            },
            "chosen": {"cell_precision": at["precision"], "cell_recall": at["recall"],
                       "alerts": at["alerts"], "alerts_per_day": round(at["alerts"] / n_days, 2)},
        },
        "targets": {"precision": naive["precision"], "recall": naive["recall"],
                    "basis": "наивная планка «событие было вчера» на отложенном году"},
        "model_version": f"fire-{chosen['name']}-budget{window}",
    }
    joblib.dump(model, str(train.MODEL))
    train.METRICS.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float),
                             encoding="utf-8")
    t = result["test"]
    print(f"итог {chosen['name']}: {len(model.boosters)} моделей, отложенный год {n_days} суток, "
          f"база {t['base']}, PR-AUC {t['pr_auc']}")
    print(f"  правило  тревог {naive['alerts']}, precision {naive['precision']}, recall {naive['recall']}")
    print(f"  бюджет   тревог {at['alerts']}, precision {at['precision']}, recall {at['recall']}")
    print(f"  порог    тревог {fixed['alerts']}, precision {fixed['precision']}, recall {fixed['recall']}")
    for name, s in seasons.items():
        print(f"  {name}: база {s['base']}, правило {s['naive']['precision']}/{s['naive']['recall']}, "
              f"модель {s['model']['precision']}/{s['model']['recall']}")
    print(f"  выше правила по обоим: {beats}, SHAP {gap:.1e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("variant")
    v.add_argument("name")
    v.add_argument("--groups", required=True)
    v.add_argument("--params", default="")
    v.add_argument("--kind", choices=("search", "measure"), default="search")
    v.add_argument("--note", default="")
    b = sub.add_parser("budget")
    b.add_argument("name")
    f = sub.add_parser("final")
    f.add_argument("name")
    f.add_argument("--reopen", default="")
    args = parser.parse_args()
    {"variant": train.cmd_variant, "budget": cmd_budget, "final": cmd_final}[args.cmd](args)


if __name__ == "__main__":
    main()
