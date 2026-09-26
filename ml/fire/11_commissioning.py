"""Отложенный год без объекта в пусконаладке. ADR 0015.

Объект 5962 «объект Гамма ПС» дал первый сигнал 2025-12-01 и 20 % событий
отложенного года. Заказчик подтвердил: это пусконаладка новой пожарной
сигнализации. Эксплуатация знает такой объект заранее, и в работе сервис не
ставил бы на нём прогнозов. Поэтому объект убирается из оценки целиком: и из
тревог модели, и из тревог правила. Скользящий бюджет считается без него.

Модель не меняется: `out/model.joblib`, вариант `wide_reg`, окно 7 суток.
Гибрид (`10_hybrid.py`) считается тем же правилом. Это третье открытие
отложенного года, и так оно помечено.

Выход: `out/commissioning.json`.
"""

import importlib
import json
import pathlib
import sys
import time

import joblib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "flood"))
sys.path.insert(0, str(HERE))
tr = importlib.import_module("06_train")
rolling = importlib.import_module("14_rolling_budget")
hyb = importlib.import_module("10_hybrid")
base = tr.base

COMMISSIONING = (5962,)
WINDOW = hyb.WINDOW
RESULT = HERE / "out" / "commissioning.json"


def main() -> None:
    con, _ = base.connect()
    model = joblib.load(HERE / "out" / "model.joblib")
    columns = model.feature_name()
    start = str(np.datetime64(base.TEST[0]) - np.timedelta64(WINDOW - 1, "D"))
    objects = con.execute(
        f"SELECT object_id FROM panel WHERE hour(hour) = 0 AND hour >= TIMESTAMP '{start}' "
        f"AND hour < TIMESTAMP '{base.TEST[1]}' ORDER BY object_id, gallery, picket, hour"
    ).df()["object_id"].to_numpy()
    data = base.load(con, columns, start, base.TEST[1])
    keep = ~np.isin(objects, COMMISSIONING)
    x, y, nv = data["x"][keep], data["y"][keep], data["naive"][keep]
    days = data["hours"][keep].astype("datetime64[D]")
    p = model.predict(x)
    young = x[:, columns.index("unit_age_days")] < hyb.YOUNG_DAYS
    f_model = rolling.rolling_alerts(p, nv, days, WINDOW)
    f_hyb = hyb.hybrid(p, nv, days, young)
    t = days >= np.datetime64(base.TEST[0])
    yt, days_t = y[t], days[t]
    months = days_t.astype("datetime64[M]").astype(int) % 12 + 1
    result = {
        "opening": "третье: объект в пусконаладке исключён по ответу заказчика",
        "excluded_objects": list(COMMISSIONING),
        "rows": int(t.sum()),
        "base": round(float(yt.mean()), 6),
        "pr_auc": round(base.pr_auc(yt, p[t]), 6),
        "rule": base.rule_point(yt, nv[t] == 1),
        "model": base.rule_point(yt, f_model[t]),
        "hybrid": base.rule_point(yt, f_hyb[t]),
        "seasons": {},
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    for name, members in (("зима", (12, 1, 2)), ("весна", (3, 4, 5)),
                          ("лето", (6, 7, 8)), ("осень", (9, 10, 11))):
        m = np.isin(months, members)
        result["seasons"][name] = {
            "rule": base.rule_point(yt[m], nv[t][m] == 1),
            "model": base.rule_point(yt[m], f_model[t][m]),
        }
    for key in ("model", "hybrid"):
        r, m = result["rule"], result[key]
        result[f"{key}_beats_rule"] = m["precision"] > r["precision"] and m["recall"] > r["recall"]
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"без объектов {COMMISSIONING}: база {result['base']}, PR-AUC {result['pr_auc']}")
    for key in ("rule", "model", "hybrid"):
        r = result[key]
        print(f"  {key}: тревог {r['alerts']}, попаданий {r['hits']}, "
              f"precision {r['precision']:.3f}, recall {r['recall']:.3f}")
    for name, s in result["seasons"].items():
        print(f"  {name}: правило {s['rule']['hits']}/{s['rule']['alerts']}, "
              f"модель {s['model']['hits']}/{s['model']['alerts']}")
    print(f"  модель выше правила по обоим: {result['model_beats_rule']}, "
          f"гибрид: {result['hybrid_beats_rule']}")


if __name__ == "__main__":
    main()
