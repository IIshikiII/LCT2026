"""Выбирает постоянное число раундов вместо ранней остановки.

## Зачем

Ранняя остановка обрывает обучение по проверочному отрезку, и число деревьев
скачет от жребия. Замер на чистой панели: полный набор даёт 7, 121 и 155
деревьев на трёх семенах, набор без признаков снятия охраны даёт 38, 27 и 118.
Разброс качества при этом сравним с разностью наборов, поэтому сравнить наборы
нельзя.

`06_ablation.py` уже снимает эту помеху постоянным бюджетом раундов. Скрипт
переносит тот же приём на суточную модель и выбирает само число.

## Как выбирается число

Скрипт обучает модель без ранней остановки на `MAX_ROUNDS` раундов, по одному
прогону на семя. Он пишет кривую средней точности на проверочном отрезке и
берёт вершину усреднённой кривой.

Вершину выбирает **только проверочный отрезок**. Отложенная выборка в выборе не
участвует: она печатается рядом, чтобы было видно цену выбора, но числом
решения не является. Выбор бюджета по отложенной выборке означал бы подгонку
под замер, которым мы потом хвалимся.

## Запуск

Порядок обязателен: сначала `07_daily_panel.py`, потом `08_train_daily.py`,
потом этот скрипт. Он читает отобранные признаки из `out/daily_metrics.json`.

    docker run --rm -v <repo>:/repo -w /repo/ml/access arm-ml:3.12 python -u 10_rounds.py
    .venv/bin/python ml/access/10_rounds.py
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import time

import duckdb
import lightgbm as lgb
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
METRICS = OUT / "daily_metrics.json"
ROUNDS = OUT / "daily_rounds.json"

_spec = importlib.util.spec_from_file_location("train_daily", HERE / "08_train_daily.py")
train_daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train_daily)

SEEDS = (train_daily.SEED, 1301, 7717)
MAX_ROUNDS = 600
# Шаг решётки бюджета. Вершина кривой лежит на плато, и точный раунд смысла не
# имеет. Круглый шаг делает число читаемым в ADR.
STEP = 10


def main() -> None:
    selected = json.loads(METRICS.read_text(encoding="utf-8"))["selected"]

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    columns = train_daily.load(con)
    data = {n: train_daily.split(con, n, columns) for n in ("train", "valid", "test")}
    index = [columns.index(c) for c in selected]
    print(f"признаков {len(selected)}, раундов до {MAX_ROUNDS}, семян {len(SEEDS)}")

    valid_curves, test_curves = [], []
    for seed in SEEDS:
        t = time.time()
        train_daily.PARAMS["seed"] = seed
        evals: dict = {}
        lgb.train(
            train_daily.PARAMS,
            lgb.Dataset(data["train"]["x"][:, index], label=data["train"]["y"],
                        feature_name=selected),
            num_boost_round=MAX_ROUNDS,
            valid_sets=[
                lgb.Dataset(data["valid"]["x"][:, index], label=data["valid"]["y"],
                            feature_name=selected),
                lgb.Dataset(data["test"]["x"][:, index], label=data["test"]["y"],
                            feature_name=selected),
            ],
            valid_names=["valid", "test"],
            callbacks=[lgb.record_evaluation(evals)],
        )
        valid_curves.append(evals["valid"]["average_precision"])
        test_curves.append(evals["test"]["average_precision"])
        print(f"  семя {seed}: {time.time() - t:.0f} c")
    train_daily.PARAMS["seed"] = train_daily.SEED

    vc = np.array(valid_curves)
    tc = np.array(test_curves)
    valid_mean = vc.mean(axis=0)
    test_mean = tc.mean(axis=0)
    # Размах между семенами на том же раунде. Бюджет, где размах велик, даёт
    # замер, который не повторяется, и брать его нельзя даже при высокой вершине.
    valid_spread = vc.max(axis=0) - vc.min(axis=0)
    test_spread = tc.max(axis=0) - tc.min(axis=0)

    # Вершину берём по решётке круглых бюджетов, а не по отдельному раунду.
    # Критерий это устойчивая вершина: среднее минус размах, и только по
    # проверочному отрезку.
    grid = [r for r in range(STEP, MAX_ROUNDS + 1, STEP)]
    best = max(grid, key=lambda r: valid_mean[r - 1] - valid_spread[r - 1])

    print(f"\n{'раунд':>7} {'AP пров.':>10} {'размах':>9} {'сред-разм':>10} "
          f"{'AP отлож.':>10} {'размах':>9}")
    for r in (10, 20, 30, 50, 75, 100, 150, 200, 300, 400, 500, 600):
        if r <= MAX_ROUNDS:
            mark = " <-" if r == best else ""
            print(f"{r:7d} {valid_mean[r - 1]:10.6f} {valid_spread[r - 1]:9.6f} "
                  f"{valid_mean[r - 1] - valid_spread[r - 1]:10.6f} "
                  f"{test_mean[r - 1]:10.6f} {test_spread[r - 1]:9.6f}{mark}")
    print(f"\nвершина проверочной кривой: {best} раундов, AP {valid_mean[best - 1]:.6f}")
    print(f"отложенная выборка там же:  AP {test_mean[best - 1]:.6f}")
    print(f"лучшая точка отложенной:    {int(np.argmax(test_mean)) + 1} раундов, "
          f"AP {test_mean.max():.6f} (в выборе не участвует)")

    ROUNDS.write_text(
        json.dumps(
            {
                "seeds": list(SEEDS),
                "max_rounds": MAX_ROUNDS,
                "step": STEP,
                "features": len(selected),
                "chosen_rounds": best,
                "valid_ap_at_chosen": round(float(valid_mean[best - 1]), 6),
                "test_ap_at_chosen": round(float(test_mean[best - 1]), 6),
                "test_ap_best": round(float(test_mean.max()), 6),
                "test_ap_best_round": int(np.argmax(test_mean)) + 1,
                "valid_spread_at_chosen": round(float(valid_spread[best - 1]), 6),
                "test_spread_at_chosen": round(float(test_spread[best - 1]), 6),
                "valid_curve_mean": [round(float(v), 6) for v in valid_mean],
                "test_curve_mean": [round(float(v), 6) for v in test_mean],
                "valid_curve_per_seed": [[round(float(v), 6) for v in c] for c in valid_curves],
                "test_curve_per_seed": [[round(float(v), 6) for v in c] for c in test_curves],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nзамер в {ROUNDS}")


if __name__ == "__main__":
    main()
