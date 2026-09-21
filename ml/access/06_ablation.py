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

## Почему рядом стоит шум жребия

`PARAMS` держат `feature_fraction` 0,9 и `bagging_fraction` 0,8. Убрать
колонку значит сменить жребий колонок и строк, поэтому PR-AUC меняется даже
тогда, когда признак не нёс ничего. Скрипт меряет этот шум прямо: обучает
полный набор на `NOISE_SEEDS` семенах и берёт размах PR-AUC. Вклад меньше
размаха читается как «признак ничего не добавил», а не как «признак вредит».

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

# Семена для замера шума жребия. Первое повторяет `train.py`.
NOISE_SEEDS = (train.SEED, 1301, 7717)


def fit(
    splits: dict[str, dict[str, object]],
    columns: list[str],
    seed: int,
) -> dict[str, float]:
    """Обучает модель на наборе колонок `columns` и меряет её на отложенной
    выборке. Возвращает число деревьев, PR-AUC и точность на полноте планки."""
    names = train.features.FEATURE_COLUMNS
    keep = [names.index(name) for name in columns]
    params = dict(train.PARAMS, seed=seed)

    started = time.time()
    booster = lgb.train(
        params,
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
    y, w = test["y"], test["w"]
    prediction = booster.predict(test["x"][:, keep], num_iteration=trees)
    naive = train.rule_point(y, w, test["naive"] == 1)
    _threshold, precision, recall, _alerts = train.weighted_scores(y, w, prediction)
    at_naive_recall = int(np.argmax(recall >= naive["recall"]))
    return {
        "trees": trees,
        "pr_auc": round(float(average_precision_score(y, prediction, sample_weight=w)), 6),
        "precision_at_naive_recall": round(float(precision[at_naive_recall]), 6),
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

    noise = [fit(splits, full_columns, seed) for seed in NOISE_SEEDS]
    full = noise[0]
    spread = round(
        max(item["pr_auc"] for item in noise) - min(item["pr_auc"] for item in noise), 6
    )
    print(
        f"полный набор: PR-AUC {full['pr_auc']:.6f}, деревьев {full['trees']}, "
        f"размах по {len(NOISE_SEEDS)} семенам {spread:.6f}"
    )

    variants = {}
    groups = [(name, [name]) for name in CANDIDATES]
    groups.append(("все три", list(CANDIDATES)))
    for title, dropped in groups:
        columns = [name for name in full_columns if name not in dropped]
        result = fit(splits, columns, train.SEED)
        result["dropped"] = dropped
        result["delta_pr_auc"] = round(full["pr_auc"] - result["pr_auc"], 6)
        result["above_noise"] = bool(abs(result["delta_pr_auc"]) > spread)
        variants[title] = result
        print(
            f"без «{title}»: PR-AUC {result['pr_auc']:.6f}, "
            f"вклад {result['delta_pr_auc']:+.6f}, "
            f"деревьев {result['trees']}, выше шума {result['above_noise']}"
        )

    report = {
        "candidates": list(CANDIDATES),
        "seeds": list(NOISE_SEEDS),
        "noise_spread_pr_auc": spread,
        "full": full,
        "variants": variants,
    }
    ABLATION.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    metrics["ablation"] = report
    METRICS.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"замер в {ABLATION} и в {METRICS} под ключом ablation")


if __name__ == "__main__":
    main()
