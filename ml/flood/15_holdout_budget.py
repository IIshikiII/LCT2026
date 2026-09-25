"""Отложенный год для скользящего бюджета тревог. Второе открытие, ADR 0012.

Отложенный год уже открывался для порога по вероятности: модель не прошла
приёмку. Правило скользящего бюджета придумано после этого открытия, а длина
окна (30 суток) выбрана на проверочных годах (`14_rolling_budget.py`). Замер
помечен как второе открытие. Порог по отложенному году не подбирается.

Прогноз даёт ансамбль из `out/model.joblib`. Первые 29 суток окна в начале
отложенного года берутся из прогнозов того же ансамбля на июнь 2025 года: это
прошлое, известное утром.

Выход: `out/holdout_budget.json`.
"""

import importlib
import json
import pathlib
import sys
import time

import joblib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
train = importlib.import_module("12_train_pu")
rolling = importlib.import_module("14_rolling_budget")

RESULT = HERE / "out" / "holdout_budget.json"
if RESULT.exists():
    raise SystemExit("второе открытие уже сделано, результат в out/holdout_budget.json")

window = json.loads((HERE / "out" / "rolling_budget.json").read_text(encoding="utf-8"))[
    "chosen_window"
]
model = joblib.load(HERE / "out" / "model.joblib")
columns = model.feature_name()
con, _ = train.base.connect()
start = str(np.datetime64(train.base.TEST[0]) - np.timedelta64(window - 1, "D"))
data = train.base.load(con, columns, start, train.base.TEST[1])
p = model.predict(data["x"])
days = data["hours"].astype("datetime64[D]")
flag = rolling.rolling_alerts(p, data["naive"], days, window)

test = days >= np.datetime64(train.base.TEST[0])
y, nv, fl = data["y"][test], data["naive"][test], flag[test]
naive = train.base.rule_point(y, nv == 1)
mdl = train.base.rule_point(y, fl)
months = days[test].astype("datetime64[M]").astype(int) % 12 + 1
seasons = {}
for name, members in (
    ("зима", (12, 1, 2)),
    ("весна", (3, 4, 5)),
    ("лето", (6, 7, 8)),
    ("осень", (9, 10, 11)),
):
    m = np.isin(months, members)
    seasons[name] = {
        "naive": train.base.rule_point(y[m], nv[m] == 1),
        "model": train.base.rule_point(y[m], fl[m]),
    }
result = {
    "opening": "второе: правило скользящего бюджета выбрано после первого открытия",
    "window_days": window,
    "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "naive": naive,
    "model": mdl,
    "beats_both": mdl["precision"] > naive["precision"]
    and mdl["recall"] > naive["recall"],
    "seasons": seasons,
}
RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"окно {window} суток, второе открытие")
print(
    f"  правило: тревог {naive['alerts']}, попаданий {naive['hits']}, "
    f"precision {naive['precision']:.3f}, recall {naive['recall']:.3f}"
)
print(
    f"  модель:  тревог {mdl['alerts']}, попаданий {mdl['hits']}, "
    f"precision {mdl['precision']:.3f}, recall {mdl['recall']:.3f}"
)
for name, s in seasons.items():
    print(
        f"  {name}: правило {s['naive']['precision']:.3f}/{s['naive']['recall']:.3f}, "
        f"модель {s['model']['precision']:.3f}/{s['model']['recall']:.3f}"
    )
print(f"  обе выше: {result['beats_both']}")
