"""Эксперимент: разброс по семенам, ансамбль семян и доверительные интервалы
замера на отложенной выборке.

Вопрос первый: насколько замер модели зависит от семени. На трёх семенах
точность в рабочей точке лежит от 0,218 до 0,266. Вопрос второй: делает ли
среднее нескольких семян модель устойчивой и не хуже одной модели. Вопрос
третий: какую разность замер на отложенной выборке вообще различает.

Скрипт обучает рабочий набор признаков из `out/daily_metrics.json` на всей
истории до 2026 года на `--seeds` семенах. Ансамбль это среднее логитов, как
`backend/app/ml/ensemble.py` (`MarginEnsemble`). Интервалы даёт бутстрэп по
суткам отложенной выборки: сутки берутся с возвращением целиком.

С ключом `--folds` скрипт ещё сравнивает одну модель и ансамбль из 5 семян на
отрезках `cv.py` по парам.

Результат: `out/seeds.json`, логиты моделей в `out/seeds_margin.npy`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/exp_seeds.py
    .venv/bin/python ml/access/exp_seeds.py --folds
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402

REPORT = cv.OUT / "seeds.json"
MARGINS = cv.OUT / "seeds_margin.npy"

SEEDS = (cv.SEED, 1301, 7717, 101, 202, 303, 404, 505, 606, 707)
ENSEMBLE_SIZES = (3, 5, 10)
BOOTSTRAP = 1000
CARDS_PER_DAY = 5


def sigmoid(m: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-m))


def point(y: np.ndarray, naive: np.ndarray, p: np.ndarray, days: int) -> dict:
    """Замер одной модели: PR-AUC, рабочая точка при полноте планки и 5 карточек в сутки."""
    naive_recall = cv.rule_point(y, naive == 1)["recall"]
    working = cv.at_recall(y, p, naive_recall)
    series = naive == 1
    return {
        "pr_auc": round(cv.pr_auc(y, p), 6),
        "precision_at_naive_recall": working["precision"],
        "alerts_per_day": round(working["alerts"] / days, 3),
        "threshold": working["threshold"],
        "recall_at_5_per_day": cv.top_k(y, p, CARDS_PER_DAY * days)["recall"],
        "series_pr_auc": round(cv.pr_auc(y[series], p[series]), 6),
    }


def bootstrap(test: cv.Panel, scores: dict[str, np.ndarray], seed: int = 0) -> dict:
    """Интервалы 95 % по бутстрэпу суток. Для каждой пары «модель и эталон»
    ещё интервал разности на тех же выборках суток."""
    rng = np.random.default_rng(seed)
    days = np.unique(test.day)
    rows_of = {d: np.nonzero(test.day == d)[0] for d in days}
    samples = {name: {"pr_auc": [], "precision": []} for name in scores}
    for _ in range(BOOTSTRAP):
        pick = rng.choice(days, size=len(days), replace=True)
        idx = np.concatenate([rows_of[d] for d in pick])
        y, naive = test.y[idx], test.naive[idx]
        if y.sum() == 0:
            continue
        naive_recall = cv.rule_point(y, naive == 1)["recall"]
        for name, p in scores.items():
            samples[name]["pr_auc"].append(cv.pr_auc(y, p[idx]))
            samples[name]["precision"].append(cv.at_recall(y, p[idx], naive_recall)["precision"])

    def ci(values: list[float]) -> list[float]:
        return [round(float(np.percentile(values, q)), 4) for q in (2.5, 50, 97.5)]

    result = {name: {k: ci(v) for k, v in s.items()} for name, s in samples.items()}
    base = next(iter(scores))
    for name in list(scores)[1:]:
        result[f"{name} минус {base}"] = {
            k: ci(np.array(samples[name][k]) - np.array(samples[base][k]))
            for k in ("pr_auc", "precision")
        }
    return result


def holdout(panel: cv.Panel, index: list[int]) -> dict:
    fit, es, test = cv.split(panel, cv.HOLDOUT_FROM, cv.HOLDOUT_FROM, None)
    days = int((test.day.max() - test.day.min()).astype(int)) + 1
    margins = []
    for seed in SEEDS:
        t = time.time()
        booster = cv.fit(fit, es, index, seed=seed)
        margins.append(booster.predict(test.x[:, index], raw_score=True))
        print(f"семя {seed}: {booster.num_trees()} деревьев, {time.time() - t:.0f} c")
    margins = np.array(margins)
    np.save(MARGINS, margins)

    single = [dict(seed=s, **point(test.y, test.naive, sigmoid(m), days))
              for s, m in zip(SEEDS, margins)]
    spread = {k: [round(min(r[k] for r in single), 4), round(max(r[k] for r in single), 4)]
              for k in ("pr_auc", "precision_at_naive_recall", "alerts_per_day")}
    ensembles = {n: point(test.y, test.naive, sigmoid(margins[:n].mean(axis=0)), days)
                 for n in ENSEMBLE_SIZES}
    for name, r in [(f"семя {single[0]['seed']}", single[0]),
                    *[(f"ансамбль {n}", r) for n, r in ensembles.items()]]:
        print(f"{name:12s} PR-AUC {r['pr_auc']:.4f}  точность {r['precision_at_naive_recall']:.3f}  "
              f"тревог в сутки {r['alerts_per_day']:.2f}")

    # Эталон шума: две одиночные модели на разных семенах. Разность между ними
    # показывает, какую разность замер не отличает от случайности.
    scores = {
        f"семя {SEEDS[0]}": sigmoid(margins[0]),
        f"семя {SEEDS[1]}": sigmoid(margins[1]),
        f"ансамбль {max(ENSEMBLE_SIZES)}": sigmoid(margins.mean(axis=0)),
    }
    return {
        "days": days,
        "events": int(test.y.sum()),
        "single": single,
        "single_spread": spread,
        "ensembles": ensembles,
        "bootstrap_days": bootstrap(test, scores),
    }


def folds(panel: cv.Panel, index: list[int], size: int = 5) -> dict:
    """Одна модель на семени `cv.SEED` против ансамбля из `size` семян на отрезках `cv.py`."""
    rows = []
    for start, end in cv.FOLDS:
        fit, es, test = cv.split(panel, start, start, end)
        margins = np.array([cv.fit(fit, es, index, seed=s).predict(test.x[:, index], raw_score=True)
                            for s in SEEDS[:size]])
        one = cv.score(test, sigmoid(margins[0]))
        ens = cv.score(test, sigmoid(margins.mean(axis=0)))
        rows.append({
            "fold": start,
            "single": {"pr_auc": one["pr_auc"],
                       "precision": one["at_naive_recall"]["precision"]},
            "ensemble": {"pr_auc": ens["pr_auc"],
                         "precision": ens["at_naive_recall"]["precision"]},
        })
        print(f"{start}: одна {one['pr_auc']:.4f}, ансамбль {ens['pr_auc']:.4f}")
    result = {"size": size, "folds": rows}
    for k in ("pr_auc", "precision"):
        diff = np.array([r["ensemble"][k] - r["single"][k] for r in rows])
        result[f"delta_{k}"] = round(float(diff.mean()), 6)
        result[f"delta_{k}_se"] = round(float(diff.std(ddof=1) / np.sqrt(len(diff))), 6)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", action="store_true", help="ещё сравнить на отрезках cv.py")
    args = parser.parse_args()

    selected = json.loads((cv.OUT / "daily_metrics.json").read_text(encoding="utf-8"))["selected"]
    panel = cv.load()
    index = [panel.columns.index(c) for c in selected]
    result = {"features": selected, "seeds": list(SEEDS), "holdout": holdout(panel, index)}
    if args.folds:
        result["folds"] = folds(panel, index)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["holdout"]["bootstrap_days"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
