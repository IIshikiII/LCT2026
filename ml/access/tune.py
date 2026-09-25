"""Подбор гиперпараметров LightGBM через Optuna. `TODO.md` §3b, шаг 6В.

Цель пробы это средний PR-AUC на отрезках `cv.py` при отобранных признаках
`out/daily_selected.json`. Отложенная выборка, первое полугодие 2026 года, в
подбор не попадает: `cv.evaluate` её не читает.

- Сэмплер `TPESampler(seed=4217)`, обрезка
  `MedianPruner(n_startup_trials=5, n_warmup_steps=2)`: слабая проба
  прерывается после третьего отрезка.
- Хранилище `sqlite:///ml/access/out/optuna.db`: прерванный поиск
  продолжается с того же места.
- Пространство поиска: `learning_rate`, `num_leaves`, `min_data_in_leaf`,
  `feature_fraction`, `bagging_fraction`, `lambda_l2`, `scale_pos_weight`.
  Остальные ключи берутся из `cv.PARAMS`.

После поиска лучшая проба перепроверяется на трёх семенах рядом с текущими
`cv.PARAMS`. Разность считается по парам «отрезок и семя». Параметры приняты,
когда средний прирост больше двух стандартных ошибок разности.

Запуск из корня репозитория:

    .venv/bin/python ml/access/tune.py --trials 60

Результат: `out/optuna.db`, `out/tune.json`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import optuna

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv

STUDY = "access_daily"
STORAGE = f"sqlite:///{(cv.OUT / 'optuna.db').as_posix()}"
RESULT = cv.OUT / "tune.json"
SELECTED = cv.OUT / "daily_selected.json"
SEEDS = (4217, 1301, 7717)


def space(trial: optuna.Trial) -> dict:
    params = dict(cv.PARAMS)
    params.update({
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 7, 127, log=True),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 50, 2000, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "lambda_l2": trial.suggest_float("lambda_l2", 0.001, 10.0, log=True),
        "scale_pos_weight": trial.suggest_float("scale_pos_weight", 1.0, 50.0, log=True),
    })
    return params


def paired(a: dict, b: dict) -> dict:
    """Разность PR-AUC `b − a` по парам «отрезок и семя»."""
    key = lambda r: (r["fold"], r["seed"])
    left = {key(r): r["pr_auc"] for r in a["runs"]}
    right = {key(r): r["pr_auc"] for r in b["runs"]}
    diff = np.array([right[k] - left[k] for k in sorted(left)])
    se = float(diff.std(ddof=1) / np.sqrt(len(diff)))
    return {
        "pairs": len(diff),
        "mean_gain": round(float(diff.mean()), 6),
        "se": round(se, 6),
        "gain_over_2se": bool(diff.mean() > 2 * se),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=60)
    args = parser.parse_args()

    selected = json.loads(SELECTED.read_text(encoding="utf-8"))["selected"]
    panel = cv.load()
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        study_name=STUDY,
        storage=STORAGE,
        load_if_exists=True,
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=4217),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=2),
    )

    def objective(trial: optuna.Trial) -> float:
        def report(step: int, value: float) -> None:
            trial.report(value, step)
            if trial.should_prune():
                raise optuna.TrialPruned()

        result = cv.evaluate(panel, selected, space(trial), report=report)
        return result["pr_auc_mean"]

    done = len([t for t in study.trials if t.state.is_finished()])
    left = max(args.trials - done, 0)
    t = time.time()
    for _ in range(left):
        study.optimize(objective, n_trials=1)
        last = study.trials[-1]
        print(f"проба {last.number:3d}: {last.state.name:8s} "
              f"{last.value if last.value is not None else '':<10} "
              f"лучшая {study.best_value:.6f}, {time.time() - t:.0f} c", flush=True)

    best = dict(cv.PARAMS)
    best.update(study.best_params)
    print(f"\nлучшая проба {study.best_trial.number}: PR-AUC {study.best_value:.6f}")
    print(json.dumps(study.best_params, ensure_ascii=False, indent=2))

    t = time.time()
    current = cv.evaluate(panel, selected, dict(cv.PARAMS), seeds=SEEDS)
    tuned = cv.evaluate(panel, selected, best, seeds=SEEDS)
    check = paired(current, tuned)
    print(f"\nтри семени, {time.time() - t:.0f} c")
    print(f"  текущие параметры   PR-AUC {current['pr_auc_mean']:.6f} ± {current['pr_auc_se']:.6f}")
    print(f"  лучшая проба        PR-AUC {tuned['pr_auc_mean']:.6f} ± {tuned['pr_auc_se']:.6f}")
    print(f"  прирост по парам    {check['mean_gain']:+.6f}, стандартная ошибка {check['se']:.6f}, "
          f"больше двух ошибок: {check['gain_over_2se']}")

    trials = [
        {"number": tr.number, "state": tr.state.name, "value": tr.value, "params": tr.params}
        for tr in study.trials
    ]
    RESULT.write_text(json.dumps({
        "study": STUDY,
        "trials_finished": len([tr for tr in study.trials if tr.state.is_finished()]),
        "trials_pruned": len([tr for tr in study.trials if tr.state.name == "PRUNED"]),
        "best_trial": study.best_trial.number,
        "best_value_single_seed": round(study.best_value, 6),
        "best_params": study.best_params,
        "check_three_seeds": {
            "current": {"pr_auc_mean": current["pr_auc_mean"], "pr_auc_se": current["pr_auc_se"]},
            "tuned": {"pr_auc_mean": tuned["pr_auc_mean"], "pr_auc_se": tuned["pr_auc_se"]},
            **check,
        },
        "trials": trials,
    }, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\nзамер в {RESULT}")


if __name__ == "__main__":
    main()
