"""Кладёт модель подтопления, классификатор воды и замер туда, откуда их читает бэкенд.

Модель это ансамбль ADR 0012 (`12_train_pu.py`, `out/model.joblib`). Тревоги
ставит скользящий бюджет на 30 суток (`14_rolling_budget.py`,
`15_holdout_budget.py`): за последние 30 суток модель поднимает столько же
тревог, сколько правило «вода была вчера». Замер рабочей точки берётся из
`out/holdout_budget.json`.

Классификатор «вода или проверка» (`11_pu_label.py export`) едет рядом: без
него бэкенд не отличит воду от плановой проверки ни в признаках, ни в метке.

Файлы в `backend/artifacts/flood_risk/`:

- `latest.joblib` — ансамбль, `app.ml.ensemble.MarginEnsemble`;
- `pu_classifier.joblib` — классификатор, `c` и граница;
- `metrics.json` — замер, раздел `decision` читает бэкенд.

Замер с дописанными полями возвращается и в `out/metrics.json`: выкладка на
сервер копирует его оттуда без Python.
"""

import json
import pathlib
import shutil

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
TARGET = HERE.parents[1] / "backend" / "artifacts" / "flood_risk"

metrics = json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))
budget = json.loads((OUT / "holdout_budget.json").read_text(encoding="utf-8"))
rolling = json.loads((OUT / "rolling_budget.json").read_text(encoding="utf-8"))
window = int(rolling["chosen_window"])
naive, model = budget["naive"], budget["model"]
days = int(metrics["test"]["days"])

decision = metrics["decision"]
decision["outcome"] = "rolling_budget"
decision["rule"] = (
    f"скользящий бюджет: за последние {window} суток тревог столько же, "
    "сколько у правила «вода была вчера»; HIGH это порог, который держит бюджет"
)
decision["alert_budget"] = {
    "window_days": window,
    "baseline": "правило «вода была вчера»",
    "fallback_threshold": decision["levels"]["HIGH"],
    "fallback_note": "порог с проверочных лет, пока истории прогнозов нет",
}
decision["chosen"] = {
    "cell_precision": model["precision"],
    "cell_recall": model["recall"],
    "alerts": model["alerts"],
    "alerts_per_day": round(model["alerts"] / days, 2),
}
metrics["test_budget"] = budget
metrics["evaluation_note"] = budget["opening"]
metrics["targets"] = {
    "precision": naive["precision"],
    "recall": naive["recall"],
    "basis": "наивная планка «вода была вчера» на отложенном году",
}
metrics["model_version"] = f"flood-pu-{metrics['variant']}-budget{window}"

TARGET.mkdir(parents=True, exist_ok=True)
shutil.copyfile(OUT / "model.joblib", TARGET / "latest.joblib")
shutil.copyfile(OUT / "pu_classifier.joblib", TARGET / "pu_classifier.joblib")
for path in (OUT / "metrics.json", TARGET / "metrics.json"):
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"модель, классификатор и замер в {TARGET}")
print(f"рабочая точка: {decision['chosen']}")
print(f"цель: {metrics['targets']}")
print(f"уровни: {decision['levels']}, бюджет {window} суток")
