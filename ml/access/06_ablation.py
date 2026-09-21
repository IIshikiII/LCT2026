"""Меряет вклад отдельного признака в качество модели доступа: обучает её без
этого признака и сравнивает с полным набором. Пишет замер в `out/ablation.json`
и кладёт его же в `out/metrics.json` под ключом `ablation`.

Скрипт отвечает на вопрос задачи T35: признак с нулевым приростом пустой на
панели или бесполезный для модели. Нулевой прирост сам по себе на этот вопрос
не отвечает. Прирост (`gain`) считается по обучению, а не по отложенной
выборке, и равен нулю в двух разных случаях: колонка держит одно значение на
всей панели, либо модель нашла разрез, но ни разу его не взяла.

## Как считается вклад

Вклад признака это разность PR-AUC полного набора и PR-AUC набора без него на
отложенной выборке. Оба числа считаются одним прогоном `train.py`: те же
отрезки, тот же отступ, те же веса отрицательной клетки.

## Две помехи сравнению и что с ними сделано

Первая помеха — жребий. `PARAMS` держат `feature_fraction` 0,9 и
`bagging_fraction` 0,8. Убрать колонку значит сменить жребий колонок и строк,
поэтому PR-AUC меняется даже тогда, когда признак не нёс ничего. Помеха
снимается повтором: каждый набор обучается на `SEEDS` семенах, и вклад
считается по среднему. Размах внутри набора идёт в замер рядом со средним.

Вторая помеха — ранняя остановка. Она обрывает обучение по проверочному
отрезку, и число деревьев скачет от 4 до 356 на соседних наборах. Прогон без
жребия останавливается на шестом дереве и даёт всем пяти наборам ровно одну
PR-AUC: до редких признаков такая модель не доходит. Помеха снимается
постоянным числом раундов `ROUNDS`: все наборы получают одинаковый бюджет
деревьев, и разность PR-AUC остаётся разностью наборов.

`ROUNDS` равно числу деревьев рабочей модели из `out/metrics.json`.

## Запуск

Порядок обязателен: сначала `train.py`, потом этот скрипт. `train.py`
переписывает `out/metrics.json` целиком и стирает ключ `ablation`.

    .venv\\Scripts\\python.exe ml\\access\\06_ablation.py      (Windows)
    .venv/bin/python ml/access/06_ablation.py                 (Ubuntu, macOS)
"""
import json
import pathlib
import time

import duckdb
import lightgbm as lgb
import numpy as np
import train
from sklearn.metrics import average_precision_score

OUT = pathlib.Path(__file__).resolve().parent / "out"
METRICS = OUT / "metrics.json"
ABLATION = OUT / "ablation.json"

# Признаки, которые проверяет T35: у них был нулевой прирост до починки окон.
CANDIDATES = ("n_alarms_1h", "neighbor_channels_1h", "has_access_sequence")

# Семена повтора. Первое повторяет `train.py`.
SEEDS = (train.SEED, 1301, 7717)

# Замер модели до починки окон. Числа взяты из `out/metrics.json` коммита
# 39fd89e^, то есть до правки `features.py`. Проверка `matches_before` ниже
# обучает набор без трёх признаков на той же панели и сравнивает с ними.
BEFORE_FIX = {"trees": 5, "pr_auc": 0.074514, "gain": dict.fromkeys(CANDIDATES, 0.0)}


def fit(
    splits: dict[str, dict[str, object]],
    columns: list[str],
    seed: int,
    rounds: int,
) -> dict[str, float]:
    """Обучает модель на наборе колонок `columns` за `rounds` раундов и меряет
    её на отложенной выборке. Ранней остановки нет: бюджет деревьев у всех
    наборов одинаковый. Возвращает PR-AUC и точность на полноте планки."""
    names = train.features.FEATURE_COLUMNS
    keep = [names.index(name) for name in columns]

    booster = lgb.train(
        dict(train.PARAMS, seed=seed),
        lgb.Dataset(
            splits["train"]["x"][:, keep],
            label=splits["train"]["y"],
            weight=splits["train"]["w"],
            feature_name=columns,
        ),
        num_boost_round=rounds,
    )

    test = splits["test"]
    y, w = test["y"], test["w"]
    prediction = booster.predict(test["x"][:, keep])
    naive = train.rule_point(y, w, test["naive"] == 1)
    _threshold, precision, recall, _alerts = train.weighted_scores(y, w, prediction)
    at_naive_recall = int(np.argmax(recall >= naive["recall"]))
    return {
        "pr_auc": round(float(average_precision_score(y, prediction, sample_weight=w)), 6),
        "precision_at_naive_recall": round(float(precision[at_naive_recall]), 6),
    }


def early_stopped(
    splits: dict[str, dict[str, object]],
    columns: list[str],
) -> dict[str, object]:
    """Повторяет прогон `train.py` слово в слово, но на наборе колонок
    `columns`: те же параметры, та же ранняя остановка, то же семя. Нужен,
    чтобы сравнить набор с замером, который лежит в `BEFORE_FIX`."""
    names = train.features.FEATURE_COLUMNS
    keep = [names.index(name) for name in columns]
    booster = lgb.train(
        train.PARAMS,
        lgb.Dataset(
            splits["train"]["x"][:, keep],
            label=splits["train"]["y"],
            weight=splits["train"]["w"],
            feature_name=columns,
        ),
        num_boost_round=train.NUM_ROUNDS,
        valid_sets=[
            lgb.Dataset(
                splits["valid"]["x"][:, keep],
                label=splits["valid"]["y"],
                weight=splits["valid"]["w"],
                feature_name=columns,
            )
        ],
        callbacks=[lgb.early_stopping(train.EARLY_STOPPING, verbose=False)],
    )
    trees = int(booster.best_iteration)
    test = splits["test"]
    prediction = booster.predict(test["x"][:, keep], num_iteration=trees)
    return {
        "trees": trees,
        "pr_auc": round(
            float(
                average_precision_score(test["y"], prediction, sample_weight=test["w"])
            ),
            6,
        ),
    }


def average(
    splits: dict[str, dict[str, object]],
    columns: list[str],
    rounds: int,
) -> dict[str, object]:
    """Обучает набор на всех семенах `SEEDS` и сводит замер к среднему,
    размаху и списку значений PR-AUC."""
    started = time.time()
    runs = [fit(splits, columns, seed, rounds) for seed in SEEDS]
    values = [item["pr_auc"] for item in runs]
    return {
        "pr_auc_mean": round(sum(values) / len(values), 6),
        "pr_auc_spread": round(max(values) - min(values), 6),
        "pr_auc_runs": values,
        "precision_at_naive_recall_mean": round(
            sum(item["precision_at_naive_recall"] for item in runs) / len(runs), 6
        ),
        "seconds": round(time.time() - started, 1),
    }


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    train.load_panel(con)

    splits = {
        "train": train.fetch_split(con, "train", train.TRAIN_END),
        "valid": train.fetch_split(con, "valid", train.VALID_END),
        "test": train.fetch_split(con, "test", train.panel_end(con)),
    }
    full_columns = list(train.features.FEATURE_COLUMNS)

    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    rounds = int(metrics["trees"])

    full = average(splits, full_columns, rounds)
    print(
        f"полный набор: PR-AUC {full['pr_auc_mean']:.6f}, "
        f"размах по {len(SEEDS)} семенам {full['pr_auc_spread']:.6f}, "
        f"раундов {rounds}"
    )

    variants = {}
    groups = [(name, [name]) for name in CANDIDATES]
    groups.append(("все три", list(CANDIDATES)))
    for title, dropped in groups:
        columns = [name for name in full_columns if name not in dropped]
        result = average(splits, columns, rounds)
        result["dropped"] = dropped
        result["delta_pr_auc"] = round(full["pr_auc_mean"] - result["pr_auc_mean"], 6)
        result["above_spread"] = bool(
            abs(result["delta_pr_auc"]) > max(full["pr_auc_spread"], result["pr_auc_spread"])
        )
        variants[title] = result
        print(
            f"без «{title}»: PR-AUC {result['pr_auc_mean']:.6f}, "
            f"вклад {result['delta_pr_auc']:+.6f}, "
            f"размах {result['pr_auc_spread']:.6f}, выше размаха {result['above_spread']}"
        )

    # Проверка равенства с замером до починки. Набор без трёх признаков на
    # сегодняшней панели это в точности вчерашний набор: до правки все три
    # колонки держали ноль на каждой строке. Совпадение PR-AUC и числа
    # деревьев подтверждает, что признаки были пустые, а не бесполезные.
    without = [name for name in full_columns if name not in CANDIDATES]
    replay = early_stopped(splits, without)
    replay["matches_before"] = bool(
        replay["trees"] == BEFORE_FIX["trees"]
        and abs(replay["pr_auc"] - BEFORE_FIX["pr_auc"]) < 1e-06
    )
    print(
        f"повтор замера до починки: PR-AUC {replay['pr_auc']:.6f}, "
        f"деревьев {replay['trees']}, совпало {replay['matches_before']}"
    )

    report = {
        "candidates": list(CANDIDATES),
        "seeds": list(SEEDS),
        "rounds": rounds,
        "before_fix": BEFORE_FIX,
        "after_fix": {
            "trees": metrics["trees"],
            "pr_auc": metrics["test"]["pr_auc"],
            "gain": {name: metrics["gain"][name] for name in CANDIDATES},
        },
        "replay_before_fix": replay,
        "full": full,
        "variants": variants,
    }
    ABLATION.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metrics["ablation"] = report
    METRICS.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"замер в {ABLATION} и в {METRICS} под ключом ablation")


if __name__ == "__main__":
    main()
