"""Примеры для презентации: события отложенной выборки, которые модель
предсказала, а наивная планка пропустила, и вклад признаков в каждое.

Скрипт не обучает модель. Он читает `out/daily_model.txt` и порог из
`out/daily_metrics.json`, поэтому запускается после `train.py`.

Результат: `out/showcase.json`. В нём абсолютные числа по рабочим точкам и
карточки событий с вкладом признаков по SHAP.

Запуск из корня репозитория:

    .venv/bin/python ml/access/showcase.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import duckdb
import lightgbm as lgb
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402
import data  # noqa: E402

METRICS = cv.OUT / "daily_metrics.json"
MODEL = cv.OUT / "daily_model.txt"
SHOWCASE = cv.OUT / "showcase.json"

CARDS = 5
TOP_FACTORS = 5
# Рабочие точки: порог HIGH и заданное число карточек в сутки.
CARDS_PER_DAY = (2, 5, 10)


def keys(path: pathlib.Path) -> np.ndarray:
    """Ключи строк панели в том же порядке, что `cv.load`."""
    con = duckdb.connect()
    return con.execute(
        f"SELECT object_id, gallery, section, day FROM read_parquet('{path.as_posix()}') "
        f"ORDER BY object_id, gallery, section, day"
    ).fetchnumpy()


def point(y: np.ndarray, naive: np.ndarray, flag: np.ndarray, days: int) -> dict:
    """Абсолютные числа одной рабочей точки против наивной планки."""
    hit = (flag == 1) & (y == 1)
    naive_hit = (naive == 1) & (y == 1)
    return {
        "events": int(y.sum()),
        "cards": int(flag.sum()),
        "cards_per_day": round(float(flag.sum()) / days, 2),
        "warned": int(hit.sum()),
        "warned_naive_missed": int((hit & ~naive_hit).sum()),
        "naive_cards": int(naive.sum()),
        "naive_warned": int(naive_hit.sum()),
        "naive_warned_model_missed": int((naive_hit & ~hit).sum()),
    }


def main() -> None:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    selected = metrics["selected"]
    threshold = float(metrics["decision"]["threshold"])

    panel = cv.load()
    k = keys(cv.PANEL)
    test_mask = panel.day >= np.datetime64(cv.HOLDOUT_FROM)
    test = panel.rows(test_mask)
    test_keys = {name: col[test_mask] for name, col in k.items()}
    index = [panel.columns.index(c) for c in selected]
    x = test.x[:, index]

    booster = lgb.Booster(model_file=str(MODEL))
    p = booster.predict(x)
    days = int((test.day.max() - test.day.min()).astype(int)) + 1

    points = {"HIGH": point(test.y, test.naive, (p >= threshold).astype(np.int8), days)}
    order = np.argsort(-p, kind="stable")
    for n in CARDS_PER_DAY:
        flag = np.zeros(len(p), dtype=np.int8)
        flag[order[: n * days]] = 1
        points[f"{n}_per_day"] = point(test.y, test.naive, flag, days)

    # События, которые модель подняла выше порога, а планка пропустила.
    chosen = np.nonzero((test.y == 1) & (test.naive == 0) & (p >= threshold))[0]
    chosen = chosen[np.argsort(-p[chosen], kind="stable")][:CARDS]

    import shap

    explainer = shap.TreeExplainer(booster)
    contributions = explainer.shap_values(x[chosen])

    sections = duckdb.connect().execute(
        f"SELECT object_id, gallery, section, list(picket ORDER BY picket) AS pickets "
        f"FROM read_parquet('{data.SECTIONS.as_posix()}') GROUP BY ALL"
    ).fetchall()
    pickets = {(o, g, s): ps for o, g, s, ps in sections}

    cards = []
    for row, contrib in zip(chosen, contributions):
        key = (
            test_keys["object_id"][row], test_keys["gallery"][row], test_keys["section"][row]
        )
        top = np.argsort(-np.abs(contrib))[:TOP_FACTORS]
        cards.append({
            "object_id": int(key[0]),
            "gallery": int(key[1]),
            "section": int(key[2]),
            "pickets": [int(v) for v in pickets.get(key, [])],
            "day": str(test_keys["day"][row])[:10],
            "probability": round(float(p[row]), 4),
            "base": round(float(test.y.mean()), 6),
            "days_since_last_event": int(test.since_armed[row]),
            "factors": [
                {
                    "feature": selected[i],
                    "value": round(float(x[row, i]), 4),
                    "shap": round(float(contrib[i]), 4),
                }
                for i in top
            ],
        })

    result = {
        "days": days,
        "threshold": threshold,
        "points": points,
        "shap_background": round(float(explainer.expected_value), 4),
        "cards": cards,
    }
    SHOWCASE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
