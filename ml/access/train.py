"""Обучает LightGBM направления «несанкционированный доступ» на обучающей
панели `out/access_features.parquet` и меряет качество на отложенной выборке.
Пишет модель в `out/model.txt` и замер в `out/metrics.json`.

Три решения этого скрипта.

## Вес отрицательной клетки

Панель прорежена: `features.py` держит все положительные клетки и долю
`NEGATIVE_KEEP_RATE` отрицательных (ADR 0001, раздел «Что из этого следует»).
Точность, посчитанная на панели как есть, завышена примерно в 50 раз: модель
видит одну отрицательную клетку вместо пятидесяти.

Поэтому каждая отрицательная клетка получает вес, а каждая положительная —
единицу. Вес считается по каждому отрезку времени отдельно:

    вес = (клеток на отрезке − положительных) / отрицательных в панели

Число клеток берётся из границ жизни единиц `out/access_units.parquet`, а не из
панели: панель отрицательных клеток целиком не держит. Вес идёт и в обучение, и
в замер. В обучении он возвращает модели настоящую базу 0,558 %, поэтому
вероятность на выходе не требует отдельной калибровки. В замере он даёт
точность и полноту такими, какими их увидит диспетчер на полной сетке.

Доля прореживания задана как 2 %, но настоящий вес получается чуть иным: жребий
отрицательных клеток идёт с повторениями, и совпадения схлопываются. Поэтому
вес считается из чисел, а не берётся как 1/0,02.

Схема весов проверена прямым счётом по полной сетке на отложенной выборке.
Полная сетка даёт 4 796 328 клеток, базу 0,944 % и наивную планку 11,220 %
точности при полноте 10,820 %. Взвешенная панель даёт то же число клеток, ту же
базу и планку 11,32 % при полноте 10,82 %. Расхождение точности в 0,1 процентного
пункта это шум жребия отрицательных клеток.

## Разбиение по времени

Три отрезка, границы по календарю:

| Отрезок | Период | Для чего |
|---|---|---|
| обучение | до 2025-07-01 | подбор деревьев |
| проверка | 2025-07-01 … 2026-01-01 | ранняя остановка |
| отложенная выборка | с 2026-01-01 | замер качества |

Отложенная выборка это последние шесть месяцев выгрузки. Она держит самый
свежий режим работы коллекторов, а модель в эксплуатации будет работать именно
с ним. Плотность событий по годам меняется в четыре раза (доля положительных
клеток панели: 42 % в 2019 году, 9 % в 2023, 32 % в 2026), поэтому случайное
разбиение дало бы модели заглянуть в будущее через соседние часы одной единицы.

Точка обучения выбывает, когда её окно метки перешагивает границу отрезка:
метка смотрит на 24 часа вперёд, и без этого отступа обучение получило бы
ответы из проверочного отрезка. Отступ снимает 24 часа перед каждой границей.

## Наивная планка на той же выборке

Планка это правило «тревога была в прошлом окне такой же длины»: прогноз равен
единице, когда у единицы есть момент тревоги метки в окне `(at − 24 ч, at]`.
Правило повторяет замер ADR 0001 (19,982 % точности на всей выгрузке), но
считается на отложенной выборке и с теми же весами, что и модель. Сравнивать
модель с числом из ADR нельзя: там другой период и другая база.

Планка берёт тревоги из `out/access_hourly_armed.parquet`, то есть из того же
источника, что метка. Признак `n_alarms_24h` для этого не годится: он считает
все тревоги, включая тревоги внутри окон «Снято с охраны».

Модель обязана бить планку, иначе она не нужна: правило «тревога была вчера»
диспетчер напишет без модели. Замер `test.beats_naive` отвечает на этот вопрос
одним значением. Сравнение идёт на равной полноте: модель ставят на порог, где
её полнота догоняет планку, и смотрят на точность.
"""
import datetime
import json
import pathlib
import time

import duckdb
import features
import lightgbm as lgb
import numpy as np
from sklearn.metrics import average_precision_score

OUT = pathlib.Path(__file__).resolve().parent / "out"
PANEL = OUT / "access_features.parquet"
UNITS = OUT / "access_units.parquet"
ARMED = OUT / "access_hourly_armed.parquet"
MODEL = OUT / "model.txt"
METRICS = OUT / "metrics.json"

# Границы отрезков. Разбор выбора — в docstring модуля.
TRAIN_END = "2025-07-01"
VALID_END = "2026-01-01"
# Отступ перед границей отрезка, часы. Равен горизонту метки.
EMBARGO_HOURS = features.HORIZON_HOURS

SEED = 4217
PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "num_threads": 8,
    "seed": SEED,
    "deterministic": True,
    "verbosity": -1,
}
NUM_ROUNDS = 600
EARLY_STOPPING = 50


def load_panel(con: duckdb.DuckDBPyConnection) -> None:
    """Подключает панель, границы жизни единиц и метку как представления
    `panel_raw`, `unit` и `armed`. Добавляет колонки `split` и `naive`."""
    con.execute(
        f"CREATE OR REPLACE VIEW panel_raw AS "
        f"SELECT * FROM read_parquet('{PANEL.as_posix()}')"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW unit AS "
        f"SELECT * FROM read_parquet('{UNITS.as_posix()}')"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW armed AS "
        f"SELECT * FROM read_parquet('{ARMED.as_posix()}')"
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW panel AS
        SELECT
            p.*,
            CASE
                WHEN p."at" < TIMESTAMP '{TRAIN_END}' THEN 'train'
                WHEN p."at" < TIMESTAMP '{VALID_END}' THEN 'valid'
                ELSE 'test'
            END AS split,
            CASE WHEN EXISTS (
                SELECT 1 FROM armed a
                WHERE (a.object_id, a.gallery, a.picket)
                    = (p.object_id, p.gallery, p.picket)
                  AND a.hour > p."at" - INTERVAL {features.HORIZON_HOURS} HOUR
                  AND a.hour <= p."at"
            ) THEN 1 ELSE 0 END AS naive
        FROM panel_raw p
        """
    )


def split_cells(
    con: duckdb.DuckDBPyConnection, t_from: str | None, t_to: str | None
) -> int:
    """Считает число клеток полной сетки «единица и час» на отрезке
    `[t_from, t_to)`. Граница `None` значит открытый конец."""
    lo = f"TIMESTAMP '{t_from}'" if t_from else "hour_from"
    hi = f"TIMESTAMP '{t_to}'" if t_to else "hour_to + INTERVAL 1 HOUR"
    return int(
        con.execute(
            f"""
            SELECT coalesce(sum(greatest(0, date_diff(
                'hour',
                greatest(hour_from, {lo}),
                least(hour_to + INTERVAL 1 HOUR, {hi})
            ))), 0)
            FROM unit
            """
        ).fetchone()[0]
    )


def panel_end(con: duckdb.DuckDBPyConnection) -> str:
    """Возвращает конец выгрузки: час после последней точки панели. Служит
    границей отступа для последнего отрезка."""
    last = con.execute('SELECT max("at") FROM panel').fetchone()[0]
    return (last + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


def fetch_split(
    con: duckdb.DuckDBPyConnection, name: str, embargo_to: str
) -> dict[str, object]:
    """Забирает отрезок панели и считает вес отрицательной клетки.

    `embargo_to` это граница следующего отрезка, а для последнего отрезка —
    конец выгрузки. Точка выбывает, когда её окно метки перешагивает границу:
    метка смотрит на 24 часа вперёд, и за границей ответа нет.
    """
    cut = f"AND \"at\" < TIMESTAMP '{embargo_to}' - INTERVAL {EMBARGO_HOURS} HOUR"
    cols = ", ".join(f'"{c}"' for c in features.FEATURE_COLUMNS)
    frame = con.execute(
        f"""
        SELECT {cols}, label, naive
        FROM panel
        WHERE split = '{name}' {cut}
        ORDER BY object_id, gallery, picket, "at"
        """
    ).df()

    bounds = con.execute(
        f'SELECT min("at"), max("at") FROM panel WHERE split = \'{name}\' {cut}'
    ).fetchone()

    label = frame["label"].to_numpy()
    n_positive = int(label.sum())
    n_negative = int(len(label) - n_positive)
    # Клетки берутся по тем же границам, что и точки отрезка, иначе вес
    # отнесёт к отрезку часы, которых в нём нет.
    cells = split_cells(
        con,
        bounds[0].strftime("%Y-%m-%d %H:%M:%S"),
        (bounds[1] + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    weight_negative = (cells - n_positive) / n_negative

    weight = np.where(label == 1, 1.0, weight_negative)
    return {
        "name": name,
        "x": frame[features.FEATURE_COLUMNS].to_numpy(dtype=np.float32),
        "y": label,
        "w": weight,
        "naive": frame["naive"].to_numpy(),
        "rows": len(label),
        "positive_rows": n_positive,
        "negative_rows": n_negative,
        "cells": cells,
        "weight_negative": weight_negative,
        "base_pct": 100.0 * n_positive / cells,
        "hours": (bounds[1] - bounds[0]).total_seconds() / 3600.0 + 1.0,
        "from": bounds[0].isoformat(sep=" "),
        "to": bounds[1].isoformat(sep=" "),
    }


def weighted_scores(
    y: np.ndarray, w: np.ndarray, prediction: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Строит взвешенную PR-кривую по порогам. Возвращает порог, точность,
    полноту и число взвешенных тревог для каждого уникального значения
    вероятности.

    Точки с одинаковой вероятностью режутся вместе: порог их не различает,
    и резать группу посередине значит завысить точность.
    """
    order = np.argsort(-prediction, kind="stable")
    p_sorted = prediction[order]
    tp = np.cumsum(w[order] * (y[order] == 1))
    fp = np.cumsum(w[order] * (y[order] == 0))
    # Последний индекс каждой группы равных вероятностей.
    ends = np.flatnonzero(np.diff(p_sorted)) if len(p_sorted) > 1 else np.array([])
    ends = np.append(ends, len(p_sorted) - 1).astype(np.int64)
    tp, fp, threshold = tp[ends], fp[ends], p_sorted[ends]
    alerts = tp + fp
    return threshold, tp / alerts, tp / tp[-1], alerts


def point(
    threshold: float,
    y: np.ndarray,
    w: np.ndarray,
    prediction: np.ndarray,
) -> dict[str, float]:
    """Меряет точность, полноту и поток тревог одного порога."""
    flag = prediction >= threshold
    tp = float(w[flag & (y == 1)].sum())
    fp = float(w[flag & (y == 0)].sum())
    positive = float(w[y == 1].sum())
    return {
        "threshold": round(float(threshold), 6),
        "precision": round(tp / (tp + fp), 6) if tp + fp else 0.0,
        "recall": round(tp / positive, 6) if positive else 0.0,
        "alerts": round(tp + fp, 1),
    }


def rule_point(y: np.ndarray, w: np.ndarray, flag: np.ndarray) -> dict[str, float]:
    """Меряет точность и полноту готового правила, у которого нет порога."""
    result = point(0.5, y, w, flag.astype(float))
    del result["threshold"]
    return result


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    load_panel(con)

    t = time.time()
    train = fetch_split(con, "train", TRAIN_END)
    valid = fetch_split(con, "valid", VALID_END)
    test = fetch_split(con, "test", panel_end(con))
    print(f"панель разобрана за {time.time() - t:.0f} c")
    for part in (train, valid, test):
        print(
            f"{part['name']}: {part['rows']} точек, "
            f"{part['positive_rows']} положительных, "
            f"клеток {part['cells']}, вес отрицательной {part['weight_negative']:.2f}, "
            f"база {part['base_pct']:.3f} %"
        )

    t = time.time()
    booster = lgb.train(
        PARAMS,
        lgb.Dataset(
            train["x"],
            label=train["y"],
            weight=train["w"],
            feature_name=features.FEATURE_COLUMNS,
        ),
        num_boost_round=NUM_ROUNDS,
        valid_sets=[
            lgb.Dataset(
                valid["x"],
                label=valid["y"],
                weight=valid["w"],
                feature_name=features.FEATURE_COLUMNS,
            )
        ],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING, verbose=False),
            lgb.log_evaluation(100),
        ],
    )
    seconds_train = time.time() - t
    print(f"обучение заняло {seconds_train:.0f} c, деревьев {booster.best_iteration}")

    y, w = test["y"], test["w"]
    prediction = booster.predict(test["x"], num_iteration=booster.best_iteration)
    pr_auc = float(average_precision_score(y, prediction, sample_weight=w))
    naive = rule_point(y, w, test["naive"] == 1)
    threshold, precision, recall, _alerts = weighted_scores(y, w, prediction)

    # Рабочие точки. Порог назначает T09, здесь он не выбирается: эти точки
    # дают ему цену ошибки в числах.
    at_naive_recall = int(np.argmax(recall >= naive["recall"]))
    f1 = np.divide(
        2 * precision * recall,
        precision + recall,
        out=np.zeros_like(precision),
        where=(precision + recall) > 0,
    )
    days = test["hours"] / 24.0
    operating = {
        "naive_recall": point(threshold[at_naive_recall], y, w, prediction),
        "max_f1": point(threshold[int(np.argmax(f1))], y, w, prediction),
    }
    for name, need, source in (
        ("precision_70", 0.70, precision),
        ("recall_50", 0.50, recall),
    ):
        reached = np.flatnonzero(source >= need)
        operating[name] = (
            point(threshold[reached[-1] if source is precision else reached[0]], y, w, prediction)
            if len(reached)
            else {}
        )
    for item in operating.values():
        if item:
            item["alerts_per_day"] = round(item["alerts"] / days, 2)
    naive["alerts_per_day"] = round(naive["alerts"] / days, 2)

    gain = booster.feature_importance("gain")
    result = {
        "direction": "UNAUTHORIZED_ACCESS",
        "horizon_hours": features.HORIZON_HOURS,
        "seed": SEED,
        "trees": int(booster.best_iteration),
        "seconds_train": round(seconds_train, 1),
        "features": features.FEATURE_COLUMNS,
        "splits": {
            part["name"]: {
                key: part[key]
                for key in (
                    "from",
                    "to",
                    "rows",
                    "positive_rows",
                    "negative_rows",
                    "cells",
                )
            }
            | {
                "weight_negative": round(part["weight_negative"], 4),
                "base_pct": round(part["base_pct"], 4),
            }
            for part in (train, valid, test)
        },
        "test": {
            "pr_auc": round(pr_auc, 6),
            "base": round(test["base_pct"] / 100.0, 6),
            "days": round(days, 1),
            "naive": naive,
            "beats_naive": bool(
                operating["naive_recall"]["precision"] > naive["precision"]
            ),
            "operating_points": operating,
        },
        "gain": {
            name: round(float(value), 1)
            for name, value in sorted(
                zip(features.FEATURE_COLUMNS, gain), key=lambda kv: -kv[1]
            )
        },
    }

    booster.save_model(str(MODEL), num_iteration=booster.best_iteration)
    METRICS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"PR-AUC {pr_auc:.4f}, база {test['base_pct'] / 100:.5f}")
    print(f"наивная планка: точность {naive['precision']:.4f}, полнота {naive['recall']:.4f}")
    print(f"модель бьёт планку: {result['test']['beats_naive']}")
    for name, item in operating.items():
        if item:
            print(
                f"{name}: порог {item['threshold']:.4f}, "
                f"точность {item['precision']:.4f}, полнота {item['recall']:.4f}, "
                f"тревог в сутки {item['alerts_per_day']}"
            )
    print(f"модель в {MODEL}, замер в {METRICS}")


if __name__ == "__main__":
    main()
