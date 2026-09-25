"""Эксперимент: вклад признаков снятия охраны (`disarm_*`) в модель.

Скрипт меряет на скользящей проверке `cv.py` отобранный набор признаков с
`disarm_*` и без них. Разность считается по парам отрезков. Разность меньше
двух её стандартных ошибок выводом не считается.

Скрипт запускают после `train.py`: он читает список признаков из
`out/daily_metrics.json`. Результат: `out/disarm_ablation.json`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/exp_disarm.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cv  # noqa: E402

METRICS = cv.OUT / "daily_metrics.json"
ABLATION = cv.OUT / "disarm_ablation.json"


def main() -> None:
    selected = json.loads(METRICS.read_text(encoding="utf-8"))["selected"]
    panel = cv.load()
    result, folds = {}, {}
    for name, keep in (
        ("полный", selected),
        ("без disarm", [c for c in selected if not c.startswith("disarm")]),
    ):
        r = cv.evaluate(panel, keep)
        folds[name] = np.array([x["pr_auc"] for x in r["runs"]])
        result[name] = {k: r[k] for k in ("features", "pr_auc_mean", "pr_auc_se",
                                          "precision_at_naive_recall_mean")}
        print(f"{name:12s} признаков {len(keep):2d}  PR-AUC {r['pr_auc_mean']:.4f} "
              f"± {r['pr_auc_se']:.4f}")
    diff = folds["полный"] - folds["без disarm"]
    delta = float(diff.mean())
    se = float(diff.std(ddof=1) / np.sqrt(len(diff)))
    result["delta_pr_auc"] = round(delta, 6)
    result["delta_se"] = round(se, 6)
    result["significant"] = bool(abs(delta) > 2 * se)
    ABLATION.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"разность {delta:+.4f} ± {se:.4f}, значима: {result['significant']}")


if __name__ == "__main__":
    main()
