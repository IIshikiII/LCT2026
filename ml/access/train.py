"""Отбирает признаки на скользящей проверке, обучает итоговую модель и меряет её
на отложенной выборке.

Проверка: шесть полугодий с 2023 по 2025 год, модуль `cv.py`. Отбор идёт так:

1. Обучить модель на всех признаках на каждом отрезке.
2. Отсортировать признаки по средней доле `gain` на отрезках.
3. Обучить модели на первых k признаках, k из `LADDER`, на каждом отрезке.
4. Взять наименьшее k, чей средний PR-AUC не ниже лучшего минус одна
   стандартная ошибка.

Итоговая модель учится на всей истории до 2026 года. Отложенная выборка,
первое полугодие 2026 года, даёт только итоговый замер:

- сравнение с наивной планкой при равной полноте и при равном числе тревог;
- тот же замер отдельно по давности прошлой тревоги на единице;
- точность и полнота при заданном числе тревог в сутки.

Прогноз на сутки T строится на 00:00 суток T − `panel.LEAD_DAYS`. Сейчас это
ноль: прогноз на ближайшие сутки.

Наивная планка: событие было на участке в последние известные сутки, то есть в
сутки T − `LEAD_DAYS` − 1.

Результат: `out/daily_model.txt`, `out/daily_model.joblib`,
`out/daily_metrics.json`, `out/daily_selected.json`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/train.py
    .venv/bin/python ml/access/train.py --panel rolling

Ключ `--panel rolling` учит модель на скользящей панели `panel.py --points`
и пишет `out/rolling_*` вместо `out/daily_*`.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import joblib
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402
import panel as panel_module  # noqa: E402

MODEL = cv.OUT / "daily_model.txt"
METRICS = cv.OUT / "daily_metrics.json"
SELECTED = cv.OUT / "daily_selected.json"
PANEL = cv.PANEL


def use_panel(kind: str) -> None:
    """Переключает вход и выход: `daily` или `rolling`."""
    global MODEL, METRICS, SELECTED, PANEL
    MODEL = cv.OUT / f"{kind}_model.txt"
    METRICS = cv.OUT / f"{kind}_metrics.json"
    SELECTED = cv.OUT / f"{kind}_selected.json"
    PANEL = cv.OUT / f"{kind}_panel.parquet"

LADDER = (10, 20, 30)
SEEDS = (cv.SEED, 1301, 7717)
# Число тревог в сутки для кривой рабочих точек. Ёмкость смены нам не
# известна, её выбирает администратор.
ALERTS_PER_DAY = (2, 5, 10, 20, 50)
# Доли строк с наибольшей вероятностью для кривой лифта и кривой накопленного
# улова событий.
LIFT_SHARES = (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)
# Группы по давности прошлой тревоги вне окна «Снято с охраны», сутки.
RECENCY = (("последние известные сутки", 1, 1), ("2–7 суток", 2, 7), ("8–30 суток", 8, 30),
           ("больше 30 суток", 31, 10**9))


def select(panel: cv.Panel) -> dict[str, object]:
    t = time.time()
    full = cv.evaluate(panel, panel.columns)
    share = np.zeros(len(panel.columns))
    for run in full["runs"]:
        gain = np.array([run["gain"][c] for c in panel.columns])
        share += gain / max(gain.sum(), 1e-12)
    share /= len(full["runs"])
    ranked = [panel.columns[i] for i in np.argsort(-share, kind="stable")]
    print(f"все признаки: PR-AUC {full['pr_auc_mean']:.4f} ± {full['pr_auc_se']:.4f}, "
          f"{time.time() - t:.0f} c")

    trials = {len(ranked): full}
    for k in LADDER:
        t = time.time()
        trials[k] = cv.evaluate(panel, ranked[:k])
        print(f"k={k:3d}: PR-AUC {trials[k]['pr_auc_mean']:.4f} ± "
              f"{trials[k]['pr_auc_se']:.4f}, {time.time() - t:.0f} c")

    best = max(trials.values(), key=lambda r: r["pr_auc_mean"])
    floor = best["pr_auc_mean"] - best["pr_auc_se"]
    k = min(k for k, r in trials.items() if r["pr_auc_mean"] >= floor)
    return {
        "ranked": ranked,
        "gain_share": {c: round(float(share[panel.columns.index(c)]), 6) for c in ranked},
        "selected": ranked[:k],
        "ladder": [
            {
                "k": k_,
                "pr_auc_mean": r["pr_auc_mean"],
                "pr_auc_se": r["pr_auc_se"],
                "precision_at_naive_recall_mean": r["precision_at_naive_recall_mean"],
                "folds": [
                    {"fold": x["fold"], "pr_auc": x["pr_auc"], "base": x["base"],
                     "trees": x["trees"]}
                    for x in r["runs"]
                ],
            }
            for k_, r in sorted(trials.items())
        ],
    }


def by_recency(test: cv.Panel, p: np.ndarray, threshold: float) -> list[dict]:
    rows = []
    for name, lo, hi in RECENCY:
        mask = (test.since_armed >= lo) & (test.since_armed <= hi)
        y, flag = test.y[mask], (p[mask] >= threshold).astype(np.int8)
        rows.append({
            "group": name,
            "rows": int(mask.sum()),
            "events": int(y.sum()),
            "model": cv.rule_point(y, flag),
            "naive": cv.rule_point(y, test.naive[mask] == 1),
        })
    return rows


def lift_curve(y: np.ndarray, p: np.ndarray, naive: np.ndarray) -> dict[str, object]:
    """Лифт это точность среди первых строк, делённая на базу. Улов это доля
    событий, пойманных первыми строками. Наивная планка даёт одну точку."""
    base = float(y.mean())
    order = np.argsort(-p, kind="stable")
    caught = np.cumsum(y[order])
    points = []
    for share in LIFT_SHARES:
        k = max(int(round(share * len(y))), 1)
        precision = float(caught[k - 1]) / k
        points.append({
            "share": share,
            "rows": k,
            "precision": round(precision, 6),
            "captured": round(float(caught[k - 1]) / float(y.sum()), 6),
            "lift": round(precision / base, 3),
        })
    naive_point = cv.rule_point(y, naive == 1)
    return {
        "base": round(base, 6),
        "pr_auc_lift": round(cv.pr_auc(y, p) / base, 3),
        "points": points,
        "naive": {
            "share": round(float((naive == 1).mean()), 6),
            "captured": naive_point["recall"],
            "lift": round(naive_point["precision"] / base, 3),
        },
    }


def shap_check(booster, x: np.ndarray) -> dict[str, object]:
    import shap

    sample = x[:20000]
    explainer = shap.TreeExplainer(booster)
    contributions = explainer.shap_values(sample)
    prob = np.clip(booster.predict(sample), 1e-12, 1 - 1e-12)
    logit = np.log(prob) - np.log(1 - prob)
    gap = float(np.max(np.abs(contributions.sum(axis=1) + explainer.expected_value - logit)))
    if gap > 1e-6:
        raise ValueError(f"SHAP не аддитивен: расхождение {gap}")
    return {"background": round(float(explainer.expected_value), 6),
            "max_additivity_gap": gap, "tolerance": 1e-6, "rows_checked": len(sample)}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", choices=("daily", "rolling"), default="daily")
    use_panel(parser.parse_args().panel)

    t = time.time()
    panel = cv.load(PANEL)
    print(f"панель: {panel.x.shape[0]} строк, {len(panel.columns)} признаков, "
          f"{time.time() - t:.0f} c")

    chosen = select(panel)
    selected = chosen["selected"]
    index = [panel.columns.index(c) for c in selected]
    print(f"\nвыбрано признаков: {len(selected)}")

    train, es, test = cv.split(panel, cv.HOLDOUT_FROM, cv.HOLDOUT_FROM, None)
    days = int((test.day.max() - test.day.min()).astype(int)) + 1

    runs, booster, p = [], None, None
    for seed in SEEDS:
        model = cv.fit(train, es, index, seed=seed)
        pred = model.predict(test.x[:, index])
        item = cv.score(test, pred)
        item.update({"seed": seed, "trees": model.num_trees()})
        runs.append(item)
        if seed == cv.SEED:
            booster, p = model, pred
    main_run = runs[0]
    naive = main_run["naive"]
    naive["alerts_per_day"] = round(naive["alerts"] / days, 2)
    working = main_run["at_naive_recall"]

    result = {
        "grid": PANEL.stem.removesuffix("_panel"),
        "horizon_hours": 24,
        "lead_hours": 24 * panel_module.LEAD_DAYS,
        "seed": cv.SEED,
        "early_stopping_metric": cv.PARAMS["metric"],
        "trees": booster.num_trees(),
        "candidates": len(panel.columns),
        "selected": selected,
        "gain_share": chosen["gain_share"],
        "cv": {"folds": [f[0] for f in cv.FOLDS], "ladder": chosen["ladder"]},
        "test": {
            "days": days,
            **{k: main_run[k] for k in ("events", "base", "pr_auc", "naive",
                                        "at_naive_recall", "at_naive_alerts")},
            "beats_naive": bool(
                working["precision"] > naive["precision"]
                and main_run["at_naive_alerts"]["recall"] > naive["recall"]
            ),
            "lift_precision": round(working["precision"] / naive["precision"], 3),
            "lift_recall": round(main_run["at_naive_alerts"]["recall"] / naive["recall"], 3),
            "by_recency": by_recency(test, p, working["threshold"]),
            "lift": lift_curve(test.y, p, test.naive),
            "curve": [
                {"alerts_per_day": n, **cv.top_k(test.y, p, n * days)}
                for n in ALERTS_PER_DAY
            ],
        },
        "stability": {
            "runs": [{"seed": r["seed"], "trees": r["trees"], "pr_auc": r["pr_auc"],
                      "precision_at_naive_recall": r["at_naive_recall"]["precision"]}
                     for r in runs],
        },
        "shap": shap_check(booster, test.x[:, index]),
    }
    spread = [r["pr_auc"] for r in runs]
    result["stability"]["pr_auc_spread"] = round(max(spread) - min(spread), 6)

    # Ключ `decision` читает бэкенд. Граница HIGH стоит в точке равной полноты с
    # планкой, CRITICAL это первый кандидат выше HIGH.
    high = float(working["threshold"])
    critical = next((c for c in (0.3, 0.4, 0.5, 0.6, 0.8) if c > high), high * 2)
    levels = {"MEDIUM": round(float(test.y.mean()), 6), "HIGH": round(high, 6),
              "CRITICAL": round(critical, 6)}
    values = [levels[n] for n in ("MEDIUM", "HIGH", "CRITICAL")]
    if values != sorted(values) or len(set(values)) != len(values):
        raise ValueError(f"границы уровней не возрастают: {levels}")
    result["decision"] = {
        "rule": "порог держит полноту наивной планки: равная полнота, выше точность",
        "threshold": levels["HIGH"],
        "levels": levels,
        "scale": "raw",
        "level_note": "границы стоят на сырой шкале бустера, калибратор отклонён",
        "chosen": {
            "cell_precision": working["precision"],
            "cell_recall": working["recall"],
            "alerts": working["alerts"],
            "alerts_per_day": round(working["alerts"] / days, 2),
        },
        "naive": naive,
    }
    result["calibration"] = {"kept": False, "method": None}

    joblib.dump(booster, str(MODEL.with_suffix(".joblib")))
    booster.save_model(str(MODEL))
    METRICS.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float),
                       encoding="utf-8")
    SELECTED.write_text(
        json.dumps({"selected": selected, "gain_share": chosen["gain_share"]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    r = result["test"]
    print(f"\nотложенная выборка, {days} суток: база {r['base']:.4%}, событий {r['events']}")
    print(f"  PR-AUC {r['pr_auc']:.4f}, семена {spread}")
    print(f"  планка:            точность {naive['precision']:.3f}, полнота {naive['recall']:.3f}")
    print(f"  при той же полноте точность {working['precision']:.3f} "
          f"(в {r['lift_precision']} раза)")
    print(f"  при том же труде   полнота {r['at_naive_alerts']['recall']:.3f} "
          f"(в {r['lift_recall']} раза)")
    for g in r["by_recency"]:
        print(f"  {g['group']:26s} событий {g['events']:4d}  модель P {g['model']['precision']:.3f} "
              f"R {g['model']['recall']:.3f}  планка P {g['naive']['precision']:.3f} "
              f"R {g['naive']['recall']:.3f}")
    for c in r["curve"]:
        print(f"  {c['alerts_per_day']:3d} тревог в сутки: точность {c['precision']:.3f}, "
              f"полнота {c['recall']:.3f}")
    print(f"\nзамер в {METRICS}")


if __name__ == "__main__":
    main()
