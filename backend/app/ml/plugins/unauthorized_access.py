"""Предиктор направления «несанкционированный доступ». Спецификация §7.

Модель — бустер LightGBM из `ml/access/train.py`, читается через
`app.ml.tracking.load_model("UNAUTHORIZED_ACCESS")`. Признаки строит
`app.features.access.build_features` (T12), имена и порядок совпадают с
`ml/access/features.py::FEATURE_COLUMNS` буква в букву.

## Порядок столбцов берёт модель, а не список в коде

`predict` и `explain` не держат свой список имён признаков: они читают
`model.feature_name()`, порядок, с которым бустер обучен (`lgb.Dataset(...,
feature_name=features.FEATURE_COLUMNS)` в `train.py`). Список признаков жил бы
тогда в трёх местах и разошёлся бы на первой же правке `app/features/access.py`.

## Ленивый импорт lightgbm и shap

Реестр `app.ml.registry.load_plugins()` импортирует этот модуль при каждом
обращении к направлению, в том числе в тестах, которые ничего не предсказывают
(`test_ml_registry.py`). `01-dependencies.md` держит LightGBM только в наборе
`research`, набор `ml` — на одном scikit-learn. Модуль обязан импортироваться
без LightGBM и SHAP, иначе регистрация плагина роняет весь реестр. Поэтому оба
импорта отложены внутрь функций, тем же приёмом, что `app/ml/tracking.py`
откладывает `joblib` и `mlflow`. Перенос LightGBM и SHAP в набор `ml` — отдельная
задача, `loop/BACKLOG.md`.

## Модели нет — предсказания нет

`load_model` уже отдаёт `None` без падения, когда файла нет ни в реестре, ни на
диске. `predict` и `explain` в этом случае поднимают `RuntimeError`: конвейер
пока не умеет пропускать направление без файла модели молча, это делает
задача T16. Ловить исключение здесь раньше времени значит скрыть дыру, а не
закрыть её.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features.access import ACCESS_ALARM_TYPES, SECURITY_ARMED, SECURITY_DISARMED
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.ml.registry import register
from app.ml.tracking import load_model

DIRECTION = "UNAUTHORIZED_ACCESS"

# Человеческая подпись признака для блока `factors`. Признак без записи здесь
# показывается своим программным именем — это деградация, а не ошибка.
FEATURE_LABELS: dict[str, str] = {
    "n_alarms_1h": "Срабатываний за последний час",
    "n_alarms_24h": "Срабатываний за 24 часа",
    "n_alarms_168h": "Срабатываний за 7 суток",
    "n_alarms_720h": "Срабатываний за 30 суток",
    "alarm_hour_share_720h": "Доля часов с тревогой за 30 суток",
    "hours_since_last_alarm": "Часов с последней тревоги",
    "night_share": "Доля тревог в ночное время",
    "hour_of_day": "Час суток",
    "day_of_week": "День недели",
    "month": "Месяц",
    "is_weekend": "Выходной день",
    "neighbor_channels_1h": "Соседних каналов пикета в тревоге",
    "is_disarmed": "Участок снят с охраны",
    "has_access_sequence": "Цепочка «дверь — датчик — движение»",
    "n_armed_alarms_24h": "Тревог вне режима охраны за 24 часа",
    "n_armed_alarms_168h": "Тревог вне режима охраны за 7 суток",
    "hours_since_last_armed_alarm": "Часов с последней тревоги вне режима охраны",
}

_MODEL_MISSING = (
    "модель направления UNAUTHORIZED_ACCESS не найдена ни в реестре, ни в "
    "ARTIFACTS_DIR; конвейер пока не умеет пропускать направление без файла "
    "модели молча, см. задачу T16"
)


def _model() -> Any | None:
    return load_model(DIRECTION)


def _row(model: Any, features: FeatureVector) -> list[list[float]]:
    return [[features[name] for name in model.feature_name()]]


class UnauthorizedAccess:
    """Предиктор направления. Спецификация §7, `docs/08-ml-plugin.md`."""

    code = DIRECTION

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        from app.features.access import build_features

        return build_features(ctx)

    def predict(self, features: FeatureVector) -> float:
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)
        return float(model.predict(_row(model, features))[0])

    def explain(self, features: FeatureVector) -> list[Block]:
        """Отдаёт блоки объяснения.

        Черновая версия: `factors` берёт вклады SHAP как есть, приведённые к
        диапазону -1..1 делением на наибольший по модулю вклад — простое
        правило, которое не сохраняет масштаб между вызовами с разным
        набором факторов. Задача T14 пересматривает правило приведения и
        добавляет блок `timeline`; докстринг с итоговым правилом будет там.

        Блок `timeseries` не может показать почасовую историю: протокол
        передаёт в `explain` только вектор признаков, без подключения к БД
        и без момента расчёта (`app/ml/protocol.py::Predictor.explain`).
        Ряд строится из уже посчитанных окон `n_alarms_*h`, а точки времени
        приближены моментом вызова `explain`, а не моментом прогноза: при
        пересчёте объяснения по сохранённому `prediction.features` эти два
        момента расходятся. T14 решает, нужен ли протоколу момент расчёта.
        """
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)

        import numpy as np
        import shap

        names = model.feature_name()
        row = np.array(_row(model, features), dtype=float)
        explainer = shap.TreeExplainer(model)
        contributions = explainer.shap_values(row)[0]

        scale = float(np.max(np.abs(contributions))) or 1.0
        ranked = sorted(zip(names, contributions, strict=True), key=lambda kv: -abs(kv[1]))
        items = [
            {
                "label": FEATURE_LABELS.get(name, name),
                "weight": max(-1.0, min(1.0, float(value) / scale)),
                "value": f"{features[name]:g}",
            }
            for name, value in ranked
        ]
        factors = Block(type="factors", title="Почему модель так решила", data={"items": items})

        now = datetime.now(UTC)
        windows_hours = (720, 168, 24, 1)
        points = [
            {
                "t": (now.replace(microsecond=0) - timedelta(hours=hours)).isoformat(),
                "v": features.get(f"n_alarms_{hours}h", 0.0),
            }
            for hours in windows_hours
        ]
        timeseries = Block(
            type="timeseries",
            title="Тревоги доступа по окнам наблюдения",
            data={
                "series": [{"name": "Срабатываний доступа", "points": points}],
                "markerAt": now.replace(microsecond=0).isoformat(),
            },
        )
        return [factors, timeseries]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        """Называет тип работ по непосредственности сигнала, не по вероятности.

        Порядок проверок от самого прямого признака проникновения к самому
        общему. Цепочка «дверь — датчик — движение» это готовый сценарий
        прохода: реагирует группа. Тревога в последний час без цепочки — это
        ещё не подтверждённый проход, сначала смотрит осмотр. Снятая охрана
        без свежей тревоги — повод проверить исправность СКУД, а не выезжать.
        Всё остальное — плановая замена замка по накопленному риску.
        """
        if features.get("has_access_sequence", 0.0) >= 1.0:
            return "Выезд группы реагирования"
        if features.get("n_alarms_1h", 0.0) >= 1.0:
            return "Осмотр люка и запорного механизма"
        if features.get("is_disarmed", 0.0) >= 1.0:
            return "Проверка СКУД"
        return "Замена замка"

    def label_rule(self, conn: Connection, facility_id: str, window: Window) -> bool:
        """Отвечает, наступило ли проникновение на объекте в окне.

        Повторяет метку обучения (ADR 0001, версия Б, закреплена в T04):
        событие — тревога доступа (`ACCESS_ALARM_TYPES`) вне окна «Снято с
        охраны». Окно открыто слева и закрыто справа, как в определении
        метки: `window.start` — момент расчёта, в метку не входит.
        """
        row = conn.execute(
            text(
                """
                WITH toggle AS (
                    SELECT occurred_at AS ts, alarm_type,
                           lead(occurred_at) OVER (ORDER BY occurred_at) AS next_ts
                    FROM alarm_event
                    WHERE facility_id = :facility_id
                      AND alarm_type IN (:armed, :disarmed)
                      AND occurred_at <= :end
                ),
                window_ AS (
                    SELECT ts AS win_start, coalesce(next_ts, :end) AS win_end
                    FROM toggle
                    WHERE alarm_type = :disarmed
                )
                SELECT 1 FROM alarm_event a
                WHERE a.facility_id = :facility_id AND a.alarm_type = ANY(:types)
                  AND a.occurred_at > :start AND a.occurred_at <= :end
                  AND NOT EXISTS (
                      SELECT 1 FROM window_ w
                      WHERE a.occurred_at >= w.win_start AND a.occurred_at < w.win_end
                  )
                LIMIT 1
                """
            ),
            {
                "facility_id": facility_id,
                "types": list(ACCESS_ALARM_TYPES),
                "armed": SECURITY_ARMED,
                "disarmed": SECURITY_DISARMED,
                "start": window.start,
                "end": window.end,
            },
        ).fetchone()
        return row is not None


register(UnauthorizedAccess())
