"""Скользящая проверка по времени. Один модуль оценки для отбора признаков,
экспериментов и подбора гиперпараметров.

Отрезки проверки: шесть полугодий с 2023 по 2025 год. Для каждого отрезка
модель учится на всей истории до его начала. Последние `ES_DAYS` суток этой
истории держат раннюю остановку и в обучение не входят. Сам отрезок проверки
ни на что не влияет, он только меряет.

Отложенная выборка, первое полугодие 2026 года, в модуль не попадает. Её
читает только `train.py` для итогового замера.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import duckdb
import lightgbm as lgb
import numpy as np

OUT = pathlib.Path(__file__).resolve().parent / "out"
PANEL = OUT / "daily_panel.parquet"

FOLDS = (
    ("2023-01-01", "2023-07-01"),
    ("2023-07-01", "2024-01-01"),
    ("2024-01-01", "2024-07-01"),
    ("2024-07-01", "2025-01-01"),
    ("2025-01-01", "2025-07-01"),
    ("2025-07-01", "2026-01-01"),
)
HOLDOUT_FROM = "2026-01-01"
ES_DAYS = 180

SEED = 4217
NUM_ROUNDS = 2000
EARLY_STOPPING = 100

# Ранняя остановка идёт по logloss. Средняя точность на редком классе шумит, и
# остановка по ней оставляла от 7 до 155 деревьев в зависимости от семени.
#
# Параметры заданы вручную. Подбор через Optuna (`tune.py`, `out/tune.json`)
# поднял PR-AUC на отрезках с 0,0782 до 0,0838, но в работу не взят: на
# отложенной выборке точность в рабочей точке упала с 0,266 до 0,214, а
# PR-AUC в среднем по трём семенам не изменился. Разбор в `DETAILS.md`.
PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
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

NOT_FEATURES = {"object_id", "gallery", "section", "day", "as_of", "label", "naive"}


@dataclass
class Panel:
    x: np.ndarray
    y: np.ndarray
    naive: np.ndarray
    day: np.ndarray
    since_armed: np.ndarray
    columns: list[str]

    def rows(self, mask: np.ndarray) -> "Panel":
        return Panel(
            self.x[mask], self.y[mask], self.naive[mask], self.day[mask],
            self.since_armed[mask], self.columns,
        )


def load(path: pathlib.Path = PANEL) -> Panel:
    """Читает панель целиком. Строки идут по единице и дню."""
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    rel = f"read_parquet('{path.as_posix()}')"
    names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
    columns = [c for c in names if c not in NOT_FEATURES]
    # Скользящая панель держит несколько строк на сутки: порядок задаёт `as_of`.
    frame = con.execute(
        f"SELECT {', '.join(columns)}, label, naive, day FROM {rel} "
        f"ORDER BY object_id, gallery, section, day, as_of"
    ).df()
    return Panel(
        x=frame[columns].to_numpy(dtype=np.float32),
        y=frame["label"].to_numpy(dtype=np.int8),
        naive=frame["naive"].to_numpy(dtype=np.int8),
        day=frame["day"].to_numpy(dtype="datetime64[D]"),
        since_armed=frame["days_since_last_armed"].to_numpy(dtype=np.int32),
        columns=columns,
    )


def split(panel: Panel, fit_to: str, test_from: str, test_to: str | None):
    """Три части: обучение, ранняя остановка, замер."""
    day = panel.day
    es_from = np.datetime64(fit_to) - np.timedelta64(ES_DAYS, "D")
    fit = panel.rows(day < es_from)
    es = panel.rows((day >= es_from) & (day < np.datetime64(fit_to)))
    test_mask = day >= np.datetime64(test_from)
    if test_to:
        test_mask &= day < np.datetime64(test_to)
    return fit, es, panel.rows(test_mask)


def fit(
    train: Panel, es: Panel, index: list[int], params: dict | None = None,
    seed: int = SEED,
) -> lgb.Booster:
    p = dict(PARAMS if params is None else params)
    p["seed"] = seed
    names = [train.columns[i] for i in index]
    return lgb.train(
        p,
        lgb.Dataset(train.x[:, index], label=train.y, feature_name=names),
        num_boost_round=NUM_ROUNDS,
        valid_sets=[lgb.Dataset(es.x[:, index], label=es.y, feature_name=names)],
        callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
    )


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Площадь под кривой «точность и полнота», метод средней точности."""
    order = np.argsort(-p, kind="stable")
    ys = y[order]
    tp = np.cumsum(ys)
    total = float(tp[-1]) if len(tp) else 0.0
    if total == 0:
        return 0.0
    return float(np.sum(ys * tp / np.arange(1, len(ys) + 1)) / total)


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


def top_k(y: np.ndarray, p: np.ndarray, alerts: int) -> dict[str, float]:
    """Точка «первые `alerts` строк по вероятности». Сравнение при равном труде."""
    order = np.argsort(-p, kind="stable")[: max(alerts, 1)]
    flag = np.zeros(len(p), dtype=np.int8)
    flag[order] = 1
    point = rule_point(y, flag)
    point["threshold"] = round(float(p[order[-1]]), 6)
    return point


def at_recall(y: np.ndarray, p: np.ndarray, recall: float) -> dict[str, float]:
    """Наименьшее число тревог, при котором полнота не ниже заданной."""
    order = np.argsort(-p, kind="stable")
    hits = np.cumsum(y[order])
    reached = np.nonzero(hits >= recall * y.sum())[0]
    alerts = int(reached[0]) + 1 if len(reached) else len(p)
    return top_k(y, p, alerts)


def score(test: Panel, p: np.ndarray) -> dict[str, object]:
    """Замер одного отрезка: PR-AUC и две точки сравнения с наивной планкой."""
    naive = rule_point(test.y, test.naive == 1)
    return {
        "events": int(test.y.sum()),
        "base": round(float(test.y.mean()), 6),
        "pr_auc": round(pr_auc(test.y, p), 6),
        "naive": naive,
        "at_naive_recall": at_recall(test.y, p, naive["recall"]),
        "at_naive_alerts": top_k(test.y, p, naive["alerts"]),
    }


def evaluate(
    panel: Panel,
    features: list[str],
    params: dict | None = None,
    seeds: tuple[int, ...] = (SEED,),
    folds: tuple[tuple[str, str], ...] = FOLDS,
    report=None,
) -> dict[str, object]:
    """Обучает модель на каждом отрезке и возвращает замеры с их средним.

    `report(step, value)` зовётся после каждого отрезка со средним PR-AUC по
    пройденным отрезкам. Optuna через него обрезает слабые пробы.
    """
    index = [panel.columns.index(name) for name in features]
    runs = []
    for step, (start, end) in enumerate(folds):
        train, es, test = split(panel, start, start, end)
        for seed in seeds:
            booster = fit(train, es, index, params, seed)
            item = score(test, booster.predict(test.x[:, index]))
            item.update({"fold": start, "seed": seed, "trees": booster.num_trees()})
            item["gain"] = dict(
                zip(features, booster.feature_importance("gain").tolist())
            )
            runs.append(item)
        if report is not None:
            report(step, float(np.mean([r["pr_auc"] for r in runs])))
    pr = np.array([r["pr_auc"] for r in runs])
    prec = np.array([r["at_naive_recall"]["precision"] for r in runs])
    return {
        "features": features,
        "pr_auc_mean": round(float(pr.mean()), 6),
        "pr_auc_se": round(float(pr.std(ddof=1) / np.sqrt(len(pr))), 6),
        "precision_at_naive_recall_mean": round(float(prec.mean()), 6),
        "runs": runs,
    }
