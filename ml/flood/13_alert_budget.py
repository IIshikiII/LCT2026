"""Правило числа тревог: сутки получают столько тревог, сколько наивное правило.

Порог по вероятности не держит число тревог, когда меняется погода года: на
отложенном году модель подняла на 58 % больше тревог, чем правило (ADR 0012).
Здесь порога нет. Каждые сутки модель поднимает ровно столько тревог, сколько в
эти сутки поднимает правило «вода была вчера», по своим самым рискованным
пикетам. Тревог поровну, поэтому больше попаданий значит выше и precision, и
recall.

Правило придумано после того, как отложенный год показал сдвиг порога. Поэтому
оно проверяется только на трёх проверочных годах. Отложенный год скрипт не
читает (ADR 0012, раздел «Правило числа тревог»).

Модель та же, что прошла отбор: вариант `recent_reg2` из
`out/variants_pu.json`, те же фолды и семена, те же прогнозы проверочных лет.

Выход: `out/alert_budget.json`.
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
RESULT = HERE / "out" / "alert_budget.json"


def budget_alerts(p: np.ndarray, naive: np.ndarray, days: np.ndarray) -> np.ndarray:
    """Отмечает в каждых сутках столько самых рискованных строк, сколько у правила."""
    flag = np.zeros(len(p), dtype=bool)
    for day in np.unique(days):
        rows = np.nonzero(days == day)[0]
        k = int(naive[rows].sum())
        if k:
            top = rows[np.argsort(-p[rows], kind="stable")[:k]]
            flag[top] = True
    return flag


chosen = next(i for i in train.read_log() if i["name"] == VARIANT)
con, all_columns = train.base.connect()
scored = train.cross_validate(con, chosen["config"], all_columns)

folds, ys, nvs, flags = [], [], [], []
for (y, nv, p), hours, meta in zip(
    scored["_oof"], scored["_oof_hours"], scored["folds"], strict=True
):
    days = hours.astype("datetime64[D]")
    flag = budget_alerts(p, nv, days)
    naive = train.base.rule_point(y, nv == 1)
    model = train.base.rule_point(y, flag)
    folds.append(
        {
            "valid": meta["valid"],
            "base": meta["base"],
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
result = {
    "variant": VARIANT,
    "rule": "тревог в сутки столько же, сколько у наивного правила",
    "folds": folds,
    "pooled": pooled,
    "holdout_read": False,
}
RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

for f in folds + [{"valid": "три года вместе", **pooled}]:
    n, m = f["naive"], f["model"]
    print(
        f"{f['valid']}: тревог {n['alerts']} и {m['alerts']}, попаданий {n['hits']} и {m['hits']}; "
        f"планка {n['precision']:.3f}/{n['recall']:.3f}, модель {m['precision']:.3f}/{m['recall']:.3f}"
        f", обе выше: {f['beats_both']}"
    )
