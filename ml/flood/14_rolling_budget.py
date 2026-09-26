"""Скользящий бюджет тревог: порог держит число тревог правила за N суток.

Правило числа тревог в сутки съело выигрыш модели: внутри суток она выбирает
те же пикеты, что правило «вода была вчера» (ADR 0012). Её сила в другом: она
лучше знает, в какие дни воды больше. Скользящий бюджет это разрешает.

Каждое утро порог ставится так, чтобы за последние N суток, включая сегодня,
у модели было столько тревог, сколько за те же N суток у правила. Всё это
известно утром: прошлые прогнозы, сегодняшние прогнозы и число пикетов с водой
вчера. Внутри окна модель перекладывает тревоги между днями, на длинной
дистанции их число держится на уровне правила даже в мокрый год.

Длина окна выбирается на трёх проверочных годах: 7, 30 или 90 суток, по числу
попаданий всех трёх лет вместе. Отложенный год скрипт не читает.

Выход: `out/rolling_budget.json`.
"""

import importlib
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
train = importlib.import_module("12_train_pu")

VARIANT = "recent_reg2"
WINDOWS = (7, 30, 90)
RESULT = HERE / "out" / "rolling_budget.json"


def rolling_alerts(
    p: np.ndarray, naive: np.ndarray, days: np.ndarray, window: int
) -> np.ndarray:
    """Тревоги по порогу, который держит бюджет правила за `window` суток."""
    flag = np.zeros(len(p), dtype=bool)
    order = np.unique(days)
    for i, day in enumerate(order):
        span = (days > day - np.timedelta64(window, "D")) & (days <= day)
        budget = int(naive[span].sum())
        today = days == day
        if budget == 0:
            continue
        scores = np.sort(p[span])[::-1]
        threshold = scores[min(budget, len(scores)) - 1]
        flag[today] = p[today] >= threshold
    return flag


def main() -> None:
    chosen = next(i for i in train.read_log() if i["name"] == VARIANT)
    con, all_columns = train.base.connect()
    scored = train.cross_validate(con, chosen["config"], all_columns)

    result = {"variant": VARIANT, "holdout_read": False, "windows": {}}
    for window in WINDOWS:
        folds, ys, nvs, flags = [], [], [], []
        for (y, nv, p), hours, meta in zip(
            scored["_oof"], scored["_oof_hours"], scored["folds"], strict=True
        ):
            days = hours.astype("datetime64[D]")
            flag = rolling_alerts(p, nv, days, window)
            naive = train.base.rule_point(y, nv == 1)
            model = train.base.rule_point(y, flag)
            folds.append(
                {
                    "valid": meta["valid"],
                    "naive": naive,
                    "model": model,
                    "beats_both": model["precision"] > naive["precision"]
                    and model["recall"] > naive["recall"],
                }
            )
            ys.append(y), nvs.append(nv), flags.append(flag)
        y, nv, flag = np.concatenate(ys), np.concatenate(nvs), np.concatenate(flags)
        pooled = {
            "naive": train.base.rule_point(y, nv == 1),
            "model": train.base.rule_point(y, flag),
        }
        pooled["beats_both"] = (
            pooled["model"]["precision"] > pooled["naive"]["precision"]
            and pooled["model"]["recall"] > pooled["naive"]["recall"]
        )
        result["windows"][str(window)] = {"folds": folds, "pooled": pooled}
        print(f"окно {window} суток")
        for f in folds + [{"valid": "три года вместе", **pooled}]:
            n, m = f["naive"], f["model"]
            print(
                f"  {f['valid']}: тревог {n['alerts']}/{m['alerts']}, попаданий {n['hits']}/{m['hits']}, "
                f"правило {n['precision']:.3f}/{n['recall']:.3f}, модель {m['precision']:.3f}/{m['recall']:.3f}, "
                f"обе выше: {f['beats_both']}"
            )

    best = max(
        result["windows"], key=lambda w: result["windows"][w]["pooled"]["model"]["hits"]
    )
    result["chosen_window"] = int(best)
    RESULT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"выбрано окно {best} суток")


if __name__ == "__main__":
    main()
