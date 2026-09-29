"""Эксперимент: эталон на самовозбуждающемся процессе (процесс Хоукса).

События доступа устроены как фон и вспышки. Событие поднимает шанс следующего,
и этот эффект затухает. Модель записывает это прямо, в суточном времени:

    λ(u, T) = exp(θ_u + θ_день_недели + θ_месяц) + α1·E1(u, T) + α2·E2(u, T)
    E_k(u, T) = Σ по суткам t < T с событием на участке u: exp(−β_k·(T − t))
    P(событие на участке u в сутки T) = 1 − exp(−λ)

Первое слагаемое это фон участка. Два других это быстрое и медленное
возбуждение от прошлых событий. Фон участка θ_u стянут к общему θ0 штрафом
L2: у редкого участка мало событий, и своя оценка у него шумит.

Скрипт отвечает на два вопроса:

1. Сколько даёт модель из 20 параметров сети и фона каждого участка против
   LightGBM на 20 признаках. Если почти столько же, бустинг по сути выучил этот
   процесс, и искать в признаках больше нечего.
2. Когда серия кончается. Период полураспада ln 2 / β и доля фона в λ дают
   ответ прямо.

β подбирается по сетке на последних `cv.ES_DAYS` сутках до начала отрезка, как
ранняя остановка в `cv.py`. Остальные параметры дают L-BFGS по правдоподобию.

Результат: `out/hawkes.json`, вероятности отложенной выборки в
`out/hawkes_holdout.parquet`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/exp_hawkes.py
"""

from __future__ import annotations

import itertools
import json
import pathlib
import sys
import time

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import lfilter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402
import data  # noqa: E402

REPORT = cv.OUT / "hawkes.json"
HOLDOUT_SCORES = cv.OUT / "hawkes_holdout.parquet"

BETA_FAST = (0.7, 1.5)
BETA_SLOW = (0.03, 0.1, 0.3)
# Штраф L2 на отклонение фона участка от общего фона.
RIDGE = 1.0
KEY = ["object_id", "gallery", "section"]


def load() -> pd.DataFrame:
    """Строки панели и сутки с событием на плотной сетке жизни участка.

    Возбуждение считается по всем суткам с событием, в том числе по суткам с
    неизвестным режимом охраны: их нет в панели, но событие в них было.
    """
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    return con.execute(
        f"""
        WITH grid AS (
            SELECT u.object_id, u.gallery, u.section, d.day::DATE AS day
            FROM read_parquet('{data.UNITS.as_posix()}') u,
                 LATERAL generate_series(date_trunc('day', u.hour_from),
                                         date_trunc('day', u.hour_to), INTERVAL 1 DAY) AS d(day)
        ),
        ev AS (
            SELECT DISTINCT object_id, gallery, section, date_trunc('day', hour)::DATE AS day
            FROM read_parquet('{data.ARMED.as_posix()}')
        )
        SELECT g.object_id, g.gallery, g.section, g.day,
               CAST(ev.day IS NOT NULL AS INTEGER) AS event,
               p.label, p.naive, p.day_of_week, p.month
        FROM grid g
        LEFT JOIN ev USING (object_id, gallery, section, day)
        LEFT JOIN read_parquet('{cv.PANEL.as_posix()}') p USING (object_id, gallery, section, day)
        ORDER BY object_id, gallery, section, day
        """
    ).df()


def excitation(frame: pd.DataFrame, beta: float) -> np.ndarray:
    """E(T) = exp(−β)·(E(T − 1) + событие(T − 1)) по каждому участку."""
    decay = np.exp(-beta)
    out = np.empty(len(frame))
    for _, idx in frame.groupby(KEY, sort=False).indices.items():
        x = frame["event"].to_numpy()[idx].astype(float)
        out[idx] = lfilter([0.0, decay], [1.0, -decay], x)
    return out


class Hawkes:
    """Параметры: θ0, θ_u, θ дня недели (6), θ месяца (11), log α1, log α2."""

    def __init__(self, n_units: int) -> None:
        self.n_units = n_units
        self.size = 1 + n_units + 6 + 11 + 2

    def split(self, w: np.ndarray):
        u0 = 1 + self.n_units
        return (w[0], w[1:u0], np.r_[0.0, w[u0:u0 + 6]], np.r_[0.0, w[u0 + 6:u0 + 17]],
                np.exp(w[u0 + 17:u0 + 19]))

    def intensity(self, w, unit, dow, month, e1, e2):
        theta0, theta_u, t_dow, t_month, alpha = self.split(w)
        background = np.exp(theta0 + theta_u[unit] + t_dow[dow] + t_month[month])
        return background, background + alpha[0] * e1 + alpha[1] * e2

    def loss(self, w, unit, dow, month, e1, e2, y):
        theta0, theta_u, _, _, alpha = self.split(w)
        background, lam = self.intensity(w, unit, dow, month, e1, e2)
        lam = np.maximum(lam, 1e-12)
        nll = np.sum((1 - y) * lam - y * np.log(-np.expm1(-lam)))
        nll += 0.5 * RIDGE * np.sum(theta_u ** 2)
        # dL/dλ = (1 − y) − y / (exp(λ) − 1)
        g_lam = (1 - y) - y / np.expm1(lam)
        g_bg = g_lam * background
        u0 = 1 + self.n_units
        grad = np.zeros(self.size)
        grad[0] = g_bg.sum()
        grad[1:u0] = np.bincount(unit, g_bg, self.n_units) + RIDGE * theta_u
        grad[u0:u0 + 6] = np.bincount(dow, g_bg, 7)[1:]
        grad[u0 + 6:u0 + 17] = np.bincount(month, g_bg, 12)[1:]
        grad[u0 + 17] = np.sum(g_lam * alpha[0] * e1)
        grad[u0 + 18] = np.sum(g_lam * alpha[1] * e2)
        return nll, grad

    def fit(self, unit, dow, month, e1, e2, y) -> np.ndarray:
        w = np.zeros(self.size)
        w[0] = np.log(max(y.mean(), 1e-6))
        w[-2:] = np.log(0.05)
        res = minimize(self.loss, w, args=(unit, dow, month, e1, e2, y), jac=True,
                       method="L-BFGS-B", options={"maxiter": 500})
        return res.x


def logloss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def run_split(frame, exc, units, fit_to: str, test_from: str, test_to: str | None) -> dict:
    """Подбор β на последних сутках до `fit_to`, обучение, замер на отрезке."""
    rows = frame["label"].notna().to_numpy()
    day = frame["day"].to_numpy(dtype="datetime64[D]")
    es_from = np.datetime64(fit_to) - np.timedelta64(cv.ES_DAYS, "D")
    fit = rows & (day < es_from)
    es = rows & (day >= es_from) & (day < np.datetime64(fit_to))
    test = rows & (day >= np.datetime64(test_from))
    if test_to:
        test &= day < np.datetime64(test_to)

    y = frame["label"].fillna(0).to_numpy()
    dow = frame["day_of_week"].fillna(0).to_numpy().astype(int) % 7
    month = (frame["month"].fillna(1).to_numpy().astype(int) - 1) % 12
    model = Hawkes(int(units.max()) + 1)

    def args(mask, b1, b2):
        return units[mask], dow[mask], month[mask], exc[b1][mask], exc[b2][mask]

    best = None
    for b1, b2 in itertools.product(BETA_FAST, BETA_SLOW):
        w = model.fit(*args(fit, b1, b2), y[fit])
        _, lam = model.intensity(w, *args(es, b1, b2))
        score = logloss(y[es], -np.expm1(-lam))
        if best is None or score < best[0]:
            best = (score, b1, b2)
    _, b1, b2 = best
    train = fit | es
    w = model.fit(*args(train, b1, b2), y[train])
    background, lam = model.intensity(w, *args(test, b1, b2))
    p = -np.expm1(-lam)

    naive = frame["naive"].fillna(0).to_numpy()[test].astype(np.int8)
    yt = y[test].astype(np.int8)
    days = len(np.unique(day[test]))
    item = cv.score(cv.Panel(np.empty((len(yt), 0)), yt, naive, day[test],
                             np.zeros(len(yt), dtype=np.int32), []), p)
    series = naive == 1
    alpha = model.split(w)[4]
    item.update({
        "beta": [b1, b2],
        "alpha": [round(float(a), 6) for a in alpha],
        "half_life_days": [round(float(np.log(2) / b), 2) for b in (b1, b2)],
        # Ожидаемое число последующих событий от одного события.
        "branching": round(float(sum(a * np.exp(-b) / (1 - np.exp(-b))
                                     for a, b in zip(alpha, (b1, b2)))), 4),
        "series_pr_auc": round(cv.pr_auc(yt[series], p[series]), 6),
        "alerts_per_day": round(item["at_naive_recall"]["alerts"] / days, 3),
        "recall_at_5_per_day": cv.top_k(yt, p, 5 * days)["recall"],
        # Доля фона в интенсивности на строках «событие было вчера».
        "background_share_series": round(float(np.median(background[series] / lam[series])), 4),
    })
    return {"item": item, "p": p, "test": test, "background": background, "lam": lam}


def main() -> None:
    t = time.time()
    frame = load()
    units = frame.groupby(KEY, sort=False).ngroup().to_numpy()
    exc = {b: excitation(frame, b) for b in (*BETA_FAST, *BETA_SLOW)}
    print(f"сетка {len(frame)} строк, {time.time() - t:.0f} c")

    gbm = json.loads((cv.OUT / "daily_metrics.json").read_text(encoding="utf-8"))
    result = {"folds": [], "gbm_holdout_pr_auc": gbm["test"]["pr_auc"],
              "gbm_holdout_precision": gbm["test"]["at_naive_recall"]["precision"]}
    for start, end in cv.FOLDS:
        r = run_split(frame, exc, units, start, start, end)["item"]
        result["folds"].append({"fold": start, **{k: r[k] for k in (
            "pr_auc", "beta", "half_life_days", "branching", "series_pr_auc")},
            "precision_at_naive_recall": r["at_naive_recall"]["precision"]})
        print(f"{start}: PR-AUC {r['pr_auc']:.4f}, β {r['beta']}, ветвление {r['branching']}")
    pr = np.array([f["pr_auc"] for f in result["folds"]])
    result["folds_pr_auc_mean"] = round(float(pr.mean()), 6)
    result["folds_pr_auc_se"] = round(float(pr.std(ddof=1) / np.sqrt(len(pr))), 6)

    h = run_split(frame, exc, units, cv.HOLDOUT_FROM, cv.HOLDOUT_FROM, None)
    item = h["item"]
    result["holdout"] = {k: item[k] for k in (
        "events", "base", "pr_auc", "naive", "at_naive_recall", "at_naive_alerts", "beta",
        "alpha", "half_life_days", "branching", "series_pr_auc", "alerts_per_day",
        "recall_at_5_per_day", "background_share_series")}
    print(f"отложенная: PR-AUC {item['pr_auc']:.4f} против {result['gbm_holdout_pr_auc']:.4f} у LightGBM, "
          f"точность {item['at_naive_recall']['precision']:.3f}")

    out = frame.loc[h["test"], KEY + ["day", "label", "naive"]].copy()
    out["p"] = h["p"]
    out["background_share"] = h["background"] / h["lam"]
    out.to_parquet(HOLDOUT_SCORES)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float) + "\n",
                      encoding="utf-8")


if __name__ == "__main__":
    main()
