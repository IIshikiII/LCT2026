"""Гибрид: модель на старых пикетах, правило на молодых. ADR 0015.

Идея появилась после открытия отложенного года. Среди его событий 19,6 %
пришлось на пикеты моложе года, на проверочных годах 7,9 %. У молодого пикета
нет истории, на которую опирается модель. Поэтому замер на отложенном году
здесь второе открытие, и так он и помечен.

Правило гибрида записано до замера и не меняется по результату.

1. Пикет молодой, если со дня первой записи его датчиков прошло меньше 365
   суток. Порог это граница «моложе года» из разбора, он не подбирается.
2. На молодых пикетах тревогу ставит правило «событие было вчера».
3. На старых пикетах тревоги ставит модель `wide_reg` скользящим бюджетом 7
   суток. Бюджет равен числу тревог правила на старых пикетах за те же сутки.

Сначала гибрид меряется на трёх проверочных годах по прогнозам моделей фолдов,
затем на отложенном году по итоговому ансамблю.

Выход: `out/hybrid.json`.
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
base = tr.base

VARIANT = "wide_reg"
WINDOW = 7
YOUNG_DAYS = 365
RESULT = HERE / "out" / "hybrid.json"


def hybrid(p, naive, days, young):
    flag = naive.astype(bool) & young
    old = ~young
    flag[old] = rolling.rolling_alerts(p[old], naive[old], days[old], WINDOW)
    return flag


def point(y, flag):
    return base.rule_point(y, flag)


def main() -> None:
    con, all_columns = base.connect()
    chosen = next(i for i in tr.train.read_log() if i["name"] == VARIANT)
    scored = tr.train.cross_validate(con, chosen["config"], all_columns)
    columns = scored["columns"]
    age = columns.index("unit_age_days")

    result = {"variant": VARIANT, "window_days": WINDOW, "young_days": YOUNG_DAYS,
              "validation": [], "holdout_opening": "второе: идея гибрида появилась после открытия"}
    lo_hi = [f["valid"].split("..") for f in scored["folds"]]
    ys, fl_rule, fl_model, fl_hyb = [], [], [], []
    for (y, nv, p), hours, (lo, hi) in zip(scored["_oof"], scored["_oof_hours"], lo_hi, strict=True):
        x = base.load(con, columns, lo, hi)["x"]
        young = x[:, age] < YOUNG_DAYS
        days = hours.astype("datetime64[D]")
        f_model = rolling.rolling_alerts(p, nv, days, WINDOW)
        f_hyb = hybrid(p, nv, days, young)
        item = {"valid": f"{lo}..{hi}", "young_event_share": round(float(y[young].sum() / y.sum()), 3),
                "rule": point(y, nv == 1), "model": point(y, f_model), "hybrid": point(y, f_hyb)}
        result["validation"].append(item)
        ys.append(y), fl_rule.append(nv == 1), fl_model.append(f_model), fl_hyb.append(f_hyb)
    y = np.concatenate(ys)
    result["validation_pooled"] = {
        "rule": point(y, np.concatenate(fl_rule)),
        "model": point(y, np.concatenate(fl_model)),
        "hybrid": point(y, np.concatenate(fl_hyb)),
    }

    model = joblib.load(HERE / "out" / "model.joblib")
    start = str(np.datetime64(base.TEST[0]) - np.timedelta64(WINDOW - 1, "D"))
    data = base.load(con, columns, start, base.TEST[1])
    p = model.predict(data["x"])
    days = data["hours"].astype("datetime64[D]")
    young = data["x"][:, age] < YOUNG_DAYS
    f_model = rolling.rolling_alerts(p, data["naive"], days, WINDOW)
    f_hyb = hybrid(p, data["naive"], days, young)
    t = days >= np.datetime64(base.TEST[0])
    yt = data["y"][t]
    result["holdout"] = {
        "young_event_share": round(float(yt[young[t]].sum() / yt.sum()), 3),
        "rule": point(yt, data["naive"][t] == 1),
        "model": point(yt, f_model[t]),
        "hybrid": point(yt, f_hyb[t]),
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def show(name, block):
        r, m, h = block["rule"], block["model"], block["hybrid"]
        print(f"{name}: тревог {r['alerts']}/{m['alerts']}/{h['alerts']}, "
              f"попаданий {r['hits']}/{m['hits']}/{h['hits']}, "
              f"precision {r['precision']:.3f}/{m['precision']:.3f}/{h['precision']:.3f}, "
              f"recall {r['recall']:.3f}/{m['recall']:.3f}/{h['recall']:.3f}")
    print("правило / модель / гибрид")
    for item in result["validation"]:
        show(item["valid"] + f" (молодых {item['young_event_share']})", item)
    show("проверочные вместе", result["validation_pooled"])
    show(f"отложенный, второе открытие (молодых {result['holdout']['young_event_share']})",
         result["holdout"])


if __name__ == "__main__":
    main()
