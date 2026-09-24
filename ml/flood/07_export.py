"""Кладёт модель и замер подтопления туда, откуда их читает бэкенд.

Плагин `backend/app/ml/plugins/flood_risk.py` берёт модель через
`app.ml.tracking.load_model("FLOOD_RISK")`, то есть из
`backend/artifacts/flood_risk/latest.joblib`, когда реестр MLflow не поднят.
Замер `metrics.json` едет рядом: его читает `app/ml/publish.py`, и число на
дашборде обязано описывать ту же модель.

Замер первой версии `06_train.py` не писал рабочую точку и цель по качеству.
Скрипт выводит их из уже посчитанных чисел отложенной выборки, не открывая её
заново: рабочая точка это счёт на пороге с проверочного отрезка, цель это
наивная планка на том же отрезке.
"""

import importlib
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
train = importlib.import_module("06_train")

OUT = HERE / "out"
TARGET = HERE.parents[1] / "backend" / "artifacts" / "flood_risk"

metrics = json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))
test = metrics["test"]
decision = metrics["decision"]
decision.setdefault(
    "chosen", train.chosen_point(test["at_valid_threshold"], test["days"])
)
metrics.setdefault("targets", train.targets_from(test["naive"]))
metrics.setdefault(
    "model_version", f"flood-{metrics['variant']}-{metrics['opened_at'][:10]}"
)

TARGET.mkdir(parents=True, exist_ok=True)
shutil.copyfile(OUT / "model.joblib", TARGET / "latest.joblib")
# Замер с дописанными полями возвращается и в `out/`: выкладка на сервер
# (`deploy/backend.sh`) копирует его оттуда без Python.
(OUT / "metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
)
(TARGET / "metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"модель и замер в {TARGET}")
print(f"рабочая точка: {decision['chosen']}")
print(f"цель: {metrics['targets']}")
