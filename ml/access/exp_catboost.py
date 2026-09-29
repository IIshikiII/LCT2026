"""Эксперимент: CatBoost против LightGBM на рабочем наборе признаков.

Прироста PR-AUC от смены бустинга мы не ждём: на 20 числовых признаках
LightGBM и CatBoost обычно расходятся меньше, чем наши семена между собой.
Скрипт проверяет две вещи, где CatBoost может дать больше:

1. Место как категория. Модель сейчас не знает объект и участок, только их
   частоты. CatBoost кодирует категорию по порядку строк и не пускает в
   кодирование метку той же строки.
2. Устойчивость к семени. Симметричные деревья и упорядоченный бустинг
   обычно меньше зависят от семени.

Три набора на одних и тех же отрезках `cv.py`, отложенной выборке и семенах:

- `lightgbm`: рабочая модель, `cv.fit`;
- `catboost`: те же 20 признаков;
- `catboost и место`: ещё объект и участок категориями.

Результат: `out/catboost.json`. Нужен пакет `catboost`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/exp_catboost.py
    .venv/bin/python ml/access/exp_catboost.py --seeds 1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402

REPORT = cv.OUT / "catboost.json"
SEEDS = (cv.SEED, 1301, 7717)
PLACE = ["place_object", "place_section"]

CATBOOST = {
    "loss_function": "Logloss",
    "iterations": cv.NUM_ROUNDS,
    "learning_rate": 0.05,
    "depth": 6,
    "l2_leaf_reg": 3.0,
    "od_type": "Iter",
    "od_wait": cv.EARLY_STOPPING,
    "thread_count": -1,
    "verbose": 0,
    "allow_writing_files": False,
}


def places() -> pd.DataFrame:
    """Объект и участок строками, в порядке строк `cv.load`."""
    return duckdb.connect().execute(
        f"""
        SELECT CAST(object_id AS VARCHAR) AS place_object,
               concat_ws('/', object_id, CAST(gallery AS INTEGER), section) AS place_section
        FROM read_parquet('{cv.PANEL.as_posix()}')
        ORDER BY object_id, gallery, section, day
        """
    ).df()


def frame(panel: cv.Panel, mask: np.ndarray, index: list[int], place: pd.DataFrame | None):
    x = pd.DataFrame(panel.x[mask][:, index], columns=[panel.columns[i] for i in index])
    if place is not None:
        for c in PLACE:
            x[c] = place[c].to_numpy()[mask]
    return x


def fit_catboost(panel, fit_mask, es_mask, index, place, seed):
    from catboost import CatBoostClassifier, Pool

    cats = PLACE if place is not None else []
    # Кодирование категории идёт по порядку строк. Строки идут по суткам, и
    # `has_time` запрещает случайные перестановки: в код места попадают только
    # прошлые сутки.
    order = np.argsort(panel.day[fit_mask], kind="stable")
    x = frame(panel, fit_mask, index, place).iloc[order]
    model = CatBoostClassifier(**CATBOOST, random_seed=seed, has_time=bool(cats))
    model.fit(
        Pool(x, panel.y[fit_mask][order], cat_features=cats),
        eval_set=Pool(frame(panel, es_mask, index, place), panel.y[es_mask], cat_features=cats),
        use_best_model=True,
    )
    return model


def masks(panel: cv.Panel, fit_to: str, test_from: str, test_to: str | None):
    day = panel.day
    es_from = np.datetime64(fit_to) - np.timedelta64(cv.ES_DAYS, "D")
    test = day >= np.datetime64(test_from)
    if test_to:
        test &= day < np.datetime64(test_to)
    return day < es_from, (day >= es_from) & (day < np.datetime64(fit_to)), test


def measure(panel, index, place, seeds, fit_to, test_from, test_to) -> dict[str, list[dict]]:
    fit_mask, es_mask, test_mask = masks(panel, fit_to, test_from, test_to)
    test = panel.rows(test_mask)
    days = len(np.unique(test.day))
    out: dict[str, list[dict]] = {"lightgbm": [], "catboost": [], "catboost и место": []}
    for seed in seeds:
        t = time.time()
        booster = cv.fit(panel.rows(fit_mask), panel.rows(es_mask), index, seed=seed)
        preds = {"lightgbm": (booster.predict(test.x[:, index]), booster.num_trees())}
        for name, pl in (("catboost", None), ("catboost и место", place)):
            model = fit_catboost(panel, fit_mask, es_mask, index, pl, seed)
            preds[name] = (model.predict_proba(frame(panel, test_mask, index, pl))[:, 1],
                           model.get_best_iteration())
        for name, (p, trees) in preds.items():
            s = cv.score(test, p)
            series = test.naive == 1
            out[name].append({
                "seed": seed, "trees": int(trees), "pr_auc": s["pr_auc"],
                "precision_at_naive_recall": s["at_naive_recall"]["precision"],
                "alerts_per_day": round(s["at_naive_recall"]["alerts"] / days, 3),
                "series_pr_auc": round(cv.pr_auc(test.y[series], p[series]), 6),
            })
        print(f"{test_from} семя {seed}: " + ", ".join(
            f"{n} {out[n][-1]['pr_auc']:.4f}" for n in out) + f"  ({time.time() - t:.0f} c)")
    return out


def summary(runs: list[dict]) -> dict:
    return {k: round(float(np.mean([r[k] for r in runs])), 5)
            for k in ("pr_auc", "precision_at_naive_recall", "alerts_per_day", "series_pr_auc")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=len(SEEDS), help="сколько семян брать")
    args = parser.parse_args()
    seeds = SEEDS[: args.seeds]

    selected = json.loads((cv.OUT / "daily_metrics.json").read_text(encoding="utf-8"))["selected"]
    panel = cv.load()
    place = places()
    index = [panel.columns.index(c) for c in selected]

    folds = [measure(panel, index, place, seeds, s, s, e) for s, e in cv.FOLDS]
    result: dict = {"features": selected, "seeds": list(seeds), "params": CATBOOST, "cv": {}}
    base = np.array([r["pr_auc"] for f in folds for r in f["lightgbm"]])
    for name in folds[0]:
        runs = [r for f in folds for r in f[name]]
        item = {"mean": summary(runs), "runs": runs}
        # Семя разбирает пары «отрезок и семя», поэтому стандартная ошибка по парам.
        if name != "lightgbm":
            diff = np.array([r["pr_auc"] for r in runs]) - base
            item["delta_pr_auc"] = round(float(diff.mean()), 6)
            item["delta_se"] = round(float(diff.std(ddof=1) / np.sqrt(len(diff))), 6)
            item["significant"] = bool(abs(item["delta_pr_auc"]) > 2 * item["delta_se"])
        # Разброс по семенам внутри отрезка: мера устойчивости.
        item["seed_spread_mean"] = round(float(np.mean(
            [np.ptp([r["pr_auc"] for r in f[name]]) for f in folds])), 6) if len(seeds) > 1 else None
        result["cv"][name] = item

    hold = measure(panel, index, place, seeds, cv.HOLDOUT_FROM, cv.HOLDOUT_FROM, None)
    result["holdout"] = {name: {"mean": summary(runs), "runs": runs} for name, runs in hold.items()}
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in hold:
        print(f"{name:18s} отрезки {result['cv'][name]['mean']}  отложенная "
              f"{result['holdout'][name]['mean']}")


if __name__ == "__main__":
    main()
