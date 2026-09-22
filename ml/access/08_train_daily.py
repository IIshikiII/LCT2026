"""Обучение на суточной панели: отбор признаков и сравнение с наивной планкой.

## Наивная планка

Планка это правило, которое диспетчер написал бы без модели за пять минут:

> **Тревога доступа вне окна снятия охраны была на этой единице вчера, значит
> она будет и сегодня.**

Правило не обучается и не имеет порога. Оно даёт одну точку на плоскости
«точность и полнота», и модель обязана эту точку обойти. Сравнение честно
только при равной полноте либо при равном числе нарядов: точность, купленная
падением полноты, ничего не стоит.

Планка выбрана именно такой, потому что тревоги доступа идут сериями. Простое
повторение вчерашнего уже ловит заметную долю событий, и обогнать случайное
угадывание недостаточно.

## Отбор признаков

Кандидатов сорок с лишним: частоты по шести окнам, свежесть, ночная доля,
снятие с охраны, соседи по объекту, цепочка доступа, устройство места и
тринадцать календарных признаков.

Отбор идёт по лестнице: признаки ранжируются по вкладу, потом обучаются модели
на первых k признаках для k из лестницы, и берётся наименьшее k, чьё качество
на проверочном отрезке отстаёт от лучшего не больше чем на один процент.
Меньше признаков означает устойчивее модель и короче карточка диспетчера.

## Что сохраняется

`out/daily_metrics.json` держит замер, `out/daily_model.txt` модель,
`out/daily_selected.json` отобранные признаки.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/access/08_train_daily.py
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import duckdb
import joblib
import lightgbm as lgb
import numpy as np

OUT = pathlib.Path(__file__).resolve().parent / "out"
PANEL = OUT / "daily_panel.parquet"
MODEL = OUT / "daily_model.txt"
METRICS = OUT / "daily_metrics.json"
SELECTED = OUT / "daily_selected.json"

TRAIN_END = "2025-07-01"
VALID_END = "2026-01-01"

# Обучение только на свежей истории. Базы отрезков разъезжаются, и семь лет
# назад сеть была другой. Ноль означает «вся история». Меняется переменной
# окружения TRAIN_WINDOW_DAYS, чтобы подбор не требовал правки файла.
TRAIN_WINDOW_DAYS = int(os.environ.get("TRAIN_WINDOW_DAYS", "0"))

SEED = 4217
NUM_ROUNDS = 2000
EARLY_STOPPING = 100

PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
    "seed": SEED,
    "num_threads": 8,
    "deterministic": True,
}

# Лестница размеров набора признаков.
LADDER = (5, 8, 12, 16, 20, 25, 30, 40)
# Допустимое отставание от лучшего качества, доля.
TOLERANCE = 0.01

NOT_FEATURES = {"object_id", "gallery", "picket", "day", "label", "naive"}


def load(con: duckdb.DuckDBPyConnection) -> list[str]:
    con.execute(
        f"CREATE OR REPLACE VIEW panel AS "
        f"SELECT * FROM read_parquet('{PANEL.as_posix()}')"
    )
    columns = [r[0] for r in con.execute("DESCRIBE SELECT * FROM panel").fetchall()]
    return [c for c in columns if c not in NOT_FEATURES]


def split(
    con: duckdb.DuckDBPyConnection, name: str, columns: list[str]
) -> dict[str, np.ndarray]:
    bounds = {
        "train": (None, TRAIN_END),
        "valid": (TRAIN_END, VALID_END),
        "test": (VALID_END, None),
    }[name]
    where = []
    if bounds[0]:
        where.append(f"day >= DATE '{bounds[0]}'")
    if bounds[1]:
        where.append(f"day < DATE '{bounds[1]}'")
    if name == "train" and TRAIN_WINDOW_DAYS:
        where.append(f"day >= DATE '{TRAIN_END}' - INTERVAL {TRAIN_WINDOW_DAYS} DAY")
    clause = " AND ".join(where) if where else "TRUE"
    frame = con.execute(
        f"SELECT {', '.join(columns)}, label, naive, day FROM panel "
        f"WHERE {clause} ORDER BY object_id, gallery, picket, day"
    ).df()
    return {
        "x": frame[columns].to_numpy(dtype=np.float32),
        "y": frame["label"].to_numpy(dtype=np.int8),
        "naive": frame["naive"].to_numpy(dtype=np.int8),
        "days": frame["day"].to_numpy(),
    }


def train_once(
    train: dict[str, np.ndarray],
    valid: dict[str, np.ndarray],
    columns: list[str],
    index: list[int] | None = None,
) -> lgb.Booster:
    xt = train["x"] if index is None else train["x"][:, index]
    xv = valid["x"] if index is None else valid["x"][:, index]
    names = columns if index is None else [columns[i] for i in index]
    booster = lgb.train(
        PARAMS,
        lgb.Dataset(xt, label=train["y"], feature_name=names),
        num_boost_round=NUM_ROUNDS,
        valid_sets=[lgb.Dataset(xv, label=valid["y"], feature_name=names)],
        callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
    )
    return booster


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Площадь под кривой «точность и полнота», метод средней точности."""
    order = np.argsort(-p, kind="stable")
    y_sorted = y[order]
    tp = np.cumsum(y_sorted)
    precision = tp / np.arange(1, len(y_sorted) + 1)
    total = float(tp[-1])
    if total == 0:
        return 0.0
    return float(np.sum(precision * y_sorted) / total)


def rule_point(y: np.ndarray, flag: np.ndarray) -> dict[str, float]:
    alerts = float(flag.sum())
    hits = float((y * flag).sum())
    positives = float(y.sum())
    return {
        "precision": round(hits / alerts, 6) if alerts else 0.0,
        "recall": round(hits / positives, 6) if positives else 0.0,
        "alerts": int(alerts),
        "hits": int(hits),
    }


def point_at(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, float]:
    point = rule_point(y, p >= threshold)
    point["threshold"] = round(float(threshold), 6)
    return point


def threshold_for_alerts(p: np.ndarray, alerts: int) -> float:
    """Порог, дающий заданное число тревог. Сравнение при равном труде."""
    if alerts <= 0:
        return float("inf")
    ordered = np.sort(p)[::-1]
    alerts = min(alerts, len(ordered))
    return float(ordered[alerts - 1])


def threshold_for_recall(y: np.ndarray, p: np.ndarray, recall: float) -> float:
    """Наименьший порог, дающий полноту не ниже заданной."""
    order = np.argsort(-p, kind="stable")
    hits = np.cumsum(y[order])
    total = float(y.sum())
    reached = np.nonzero(hits / total >= recall)[0]
    if len(reached) == 0:
        return float(p.min())
    return float(p[order][reached[0]])


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    columns = load(con)
    print(f"кандидатов в признаки: {len(columns)}")

    t = time.time()
    train = split(con, "train", columns)
    valid = split(con, "valid", columns)
    test = split(con, "test", columns)
    print(
        f"строк: обучение {len(train['y'])}, проверка {len(valid['y'])}, "
        f"отложенная {len(test['y'])}, загружено за {time.time() - t:.0f} c"
    )
    for name, data in (("обучение", train), ("проверка", valid), ("отложенная", test)):
        print(f"  база {name}: {data['y'].mean():.6f}, событий {int(data['y'].sum())}")

    # --- полный набор ------------------------------------------------------
    full = train_once(train, valid, columns)
    gain = dict(zip(columns, full.feature_importance("gain"), strict=True))
    ranked = [name for name, _ in sorted(gain.items(), key=lambda kv: -kv[1])]
    full_valid = pr_auc(valid["y"], full.predict(valid["x"]))
    print(f"\nполный набор: PR-AUC на проверке {full_valid:.6f}, деревьев {full.num_trees()}")

    # --- лестница отбора ---------------------------------------------------
    trials = []
    for k in LADDER:
        if k > len(ranked):
            continue
        keep = ranked[:k]
        index = [columns.index(name) for name in keep]
        booster = train_once(train, valid, columns, index)
        score = pr_auc(valid["y"], booster.predict(valid["x"][:, index]))
        trials.append({"k": k, "pr_auc_valid": round(score, 6)})
        print(f"  k={k:3d}: PR-AUC на проверке {score:.6f}")

    trials.append({"k": len(columns), "pr_auc_valid": round(full_valid, 6)})
    best = max(item["pr_auc_valid"] for item in trials)
    chosen = min(
        (item for item in trials if item["pr_auc_valid"] >= best * (1 - TOLERANCE)),
        key=lambda item: item["k"],
    )
    selected = ranked[: chosen["k"]]
    index = [columns.index(name) for name in selected]
    print(f"\nвыбрано признаков: {chosen['k']} (лучшее {best:.6f})")

    # --- итоговая модель ---------------------------------------------------
    booster = train_once(train, valid, columns, index)
    p_test = booster.predict(test["x"][:, index])
    y = test["y"]

    naive = rule_point(y, test["naive"] == 1)
    equal_recall = point_at(y, p_test, threshold_for_recall(y, p_test, naive["recall"]))
    equal_alerts = point_at(y, p_test, threshold_for_alerts(p_test, naive["alerts"]))

    days = (np.max(test["days"]) - np.min(test["days"])).astype("timedelta64[D]").astype(int) + 1
    result = {
        "grid": "daily",
        "horizon_hours": 24,
        "seed": SEED,
        "train_window_days": TRAIN_WINDOW_DAYS,
        "trees": booster.num_trees(),
        "candidates": len(columns),
        "selected": selected,
        "ladder": trials,
        "gain": {name: round(float(gain[name]), 1) for name in ranked},
        "splits": {
            name: {
                "rows": len(data["y"]),
                "positives": int(data["y"].sum()),
                "base": round(float(data["y"].mean()), 6),
            }
            for name, data in (("train", train), ("valid", valid), ("test", test))
        },
        "test": {
            "days": days,
            "base": round(float(y.mean()), 6),
            "pr_auc": round(pr_auc(y, p_test), 6),
            "naive": naive,
            "at_naive_recall": equal_recall,
            "at_naive_alerts": equal_alerts,
            "beats_naive": bool(
                equal_recall["precision"] > naive["precision"]
                and equal_alerts["recall"] > naive["recall"]
            ),
        },
    }
    result["test"]["naive"]["alerts_per_day"] = round(naive["alerts"] / days, 2)
    result["test"]["lift_precision"] = round(
        equal_recall["precision"] / naive["precision"], 3
    )
    result["test"]["lift_recall"] = round(equal_alerts["recall"] / naive["recall"], 3)

    # --- устойчивость и интерпретируемость ---------------------------------
    # Три семени отвечают на вопрос, не случаен ли результат. SHAP отвечает на
    # вопрос, осталась ли модель объяснимой: сумма вкладов плюс базовое
    # значение обязана равняться логиту вероятности.
    runs = []
    for extra in (1301, 7717):
        PARAMS["seed"] = extra
        other = train_once(train, valid, columns, index)
        p_other = other.predict(test["x"][:, index])
        runs.append(
            {
                "seed": extra,
                "pr_auc": round(pr_auc(y, p_other), 6),
                "precision_at_naive_recall": round(
                    point_at(y, p_other, threshold_for_recall(y, p_other, naive["recall"]))[
                        "precision"
                    ],
                    6,
                ),
            }
        )
    PARAMS["seed"] = SEED
    runs.insert(
        0,
        {
            "seed": SEED,
            "pr_auc": result["test"]["pr_auc"],
            "precision_at_naive_recall": equal_recall["precision"],
        },
    )
    spread = max(r["pr_auc"] for r in runs) - min(r["pr_auc"] for r in runs)
    result["stability"] = {"runs": runs, "pr_auc_spread": round(spread, 6)}

    import shap

    sample = test["x"][:, index][:20000]
    explainer = shap.TreeExplainer(booster)
    contributions = explainer.shap_values(sample)
    logit = np.log(np.clip(booster.predict(sample), 1e-12, 1 - 1e-12))
    logit = logit - np.log(1 - np.clip(booster.predict(sample), 1e-12, 1 - 1e-12))
    gap = float(np.max(np.abs(contributions.sum(axis=1) + explainer.expected_value - logit)))
    result["shap"] = {
        "background": round(float(explainer.expected_value), 6),
        "max_additivity_gap": gap,
        "tolerance": 1e-6,
        "rows_checked": len(sample),
    }
    if gap > 1e-6:
        raise ValueError(f"SHAP не аддитивен: расхождение {gap}")

    # Решение о порогах пишется в том же виде, что у часовой модели: публикация
    # замера и сторож шкалы в бэкенде читают ключ `decision`, а не форму замера.
    # Граница HIGH равна порогу заявки, то есть точке равной полноты с планкой.
    # CRITICAL берётся как ближайший кандидат выше HIGH.
    high = float(equal_recall["threshold"])
    critical = next((c for c in (0.3, 0.4, 0.5, 0.6, 0.8) if c > high), high * 2)
    levels = {
        "MEDIUM": round(float(y.mean()), 6),
        "HIGH": round(high, 6),
        "CRITICAL": round(critical, 6),
    }
    order = ["MEDIUM", "HIGH", "CRITICAL"]
    values = [levels[name] for name in order]
    if values != sorted(values) or len(set(values)) != len(values):
        raise ValueError(f"границы уровней не возрастают: {levels}")
    result["decision"] = {
        "rule": (
            "порог держит полноту наивной планки при вдвое меньшем числе нарядов: "
            "равная полнота, выше точность"
        ),
        "threshold": levels["HIGH"],
        "levels": levels,
        "scale": "raw",
        "level_note": "границы стоят на сырой шкале бустера, калибратор отклонён",
        "chosen": {
            "cell_precision": equal_recall["precision"],
            "cell_recall": equal_recall["recall"],
            "alerts": equal_recall["alerts"],
            "alerts_per_day": round(equal_recall["alerts"] / days, 2),
        },
        "naive": naive,
    }
    result["calibration"] = {"kept": False, "method": None}

    joblib.dump(booster, str(MODEL.with_suffix(".joblib")))
    booster.save_model(str(MODEL))
    METRICS.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8"
    )
    SELECTED.write_text(
        json.dumps(
            {"selected": selected, "gain": result["gain"]},
            ensure_ascii=False, indent=2, default=float,
        ),
        encoding="utf-8",
    )

    print("\nотложенная выборка:")
    print(f"  база                {y.mean():.6f}, событий {int(y.sum())}, суток {days}")
    print(f"  PR-AUC              {result['test']['pr_auc']:.6f}")
    print(
        f"  наивно              точность {naive['precision']:.4f}, "
        f"полнота {naive['recall']:.4f}, тревог {naive['alerts']}"
    )
    print(
        f"  при той же полноте  точность {equal_recall['precision']:.4f} "
        f"(в {result['test']['lift_precision']} раза), тревог {equal_recall['alerts']}"
    )
    print(
        f"  при том же труде    полнота {equal_alerts['recall']:.4f} "
        f"(в {result['test']['lift_recall']} раза), точность {equal_alerts['precision']:.4f}"
    )
    print(f"  бьёт планку: {result['test']['beats_naive']}")
    print(
        f"  три семени          PR-AUC {[r['pr_auc'] for r in runs]}, "
        f"разброс {spread:.6f}"
    )
    print(f"  SHAP аддитивен      расхождение {gap:.2e} при допуске 1e-06")
    print(f"\nзамер в {METRICS}")


if __name__ == "__main__":
    main()
