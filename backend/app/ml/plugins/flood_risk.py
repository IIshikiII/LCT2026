"""Предиктор направления «риск подтопления». Спецификация §7.

Модель — суточный бустер LightGBM из `ml/flood/10_train.py`, читается через
`app.ml.tracking.load_model("FLOOD_RISK")`. Признаки считает реестр
`app.features.registry` по модулю `app/features/flood.py`, набор и порядок
называет сама модель через `feature_name()`. Разбор метки и модели лежит в
ADR 0010 и ADR 0011: водой считается только сигнал вне рабочего окна, сигналы
плановых проверок в метку не идут.

## Направление считается не на каждом объекте

Модель училась на единицах, где стоит насос или датчик затопления. На объекте
без них прогноз подтопления был бы числом без смысла, и метод `applies`
отвечает «нет». Конвейер такой объект по этому направлению пропускает.

## Ленивый импорт

Как у доступа: `lightgbm` и `shap` импортируются внутри функций, поэтому
регистрация плагина не требует этих библиотек, а отсутствие модели даёт
понятный `RuntimeError`, а не `ModuleNotFoundError`.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features.flood import (
    LABEL_TYPES,
    SENSOR_FLOOD,
    SENSOR_PUMP,
    STATE_TYPES,
    WATER,
    holidays,
)
from app.ml.plugins.unauthorized_access import shap_weight
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.ml.registry import register
from app.ml.tracking import load_model

DIRECTION = "FLOOD_RISK"

# Подписи признаков для карточки диспетчера. Значение признака карточка
# показывает рядом, поэтому подпись называет величину, а не число.
FEATURE_LABELS: dict[str, str] = {
    "pump_changes_1h": "Насос переключался за последний час, раз",
    "pump_changes_6h": "Насос переключался за 6 часов, раз",
    "pump_changes_24h": "Насос переключался за сутки, раз",
    "pump_blink_hours_24h": "Часов, когда насос мигал, за сутки",
    "pump_blink_hours_168h": "Часов, когда насос мигал, за неделю",
    "pump_on_share_24h": "Доля суток, когда насос работал",
    "pump_on_share_168h": "Доля недели, когда насос работал",
    "pump_on_growth_24_720": "Насос работает чаще обычного, во сколько раз",
    "pump_unavailable_24h": "Насос обесточен или неисправен за сутки, раз",
    "pump_unavailable_168h": "Насос обесточен или неисправен за неделю, раз",
    "pump_all_running_24h": "Работали все насосы станции за сутки, раз",
    "pump_all_running_168h": "Работали все насосы станции за неделю, раз",
    "evt_hours_24h": "Часов с затоплением за сутки",
    "evt_hours_168h": "Часов с затоплением за неделю",
    "evt_hours_720h": "Часов с затоплением за 30 суток",
    "evt_hours_2160h": "Часов с затоплением за 90 суток",
    "evt_hours_8760h": "Часов с затоплением за год",
    "evt_moments_168h": "Сигналов затопления за неделю",
    "evt_hours_since_last": "Часов с последнего затопления",
    "obj_events_24h": "Сигналов затопления у соседей по объекту за сутки",
    "obj_events_168h": "Сигналов затопления у соседей по объекту за неделю",
    "obj_pump_changes_24h": "Насосы соседей по объекту переключались за сутки, раз",
    "obj_pump_blink_24h": "Часов, когда мигал насос соседа по объекту, за сутки",
    "obj_pump_unavailable_24h": "Насос соседа по объекту обесточен или неисправен за сутки",
    "obj_pump_all_running_24h": "У соседа по объекту работали все насосы за сутки, раз",
    "obj_pump_on_minutes_24h": "Минут работы насосов соседей по объекту за сутки",
    "cx_events_24h": "Сигналов затопления в соседних объектах комплекса за сутки",
    "cx_events_168h": "Сигналов затопления в соседних объектах комплекса за неделю",
    "cx_pump_blink_24h": "Часов мигания насосов в соседних объектах комплекса за сутки",
    "cx_pump_unavailable_24h": "Насосы соседних объектов комплекса недоступны за сутки",
    "cx_pump_all_running_168h": "Все насосы соседних объектов комплекса работали за неделю",
    "unit_pumps": "Насосов на пикете",
    "unit_flood_sensors": "Датчиков затопления на пикете",
    "unit_age_days": "Суток наблюдения пикета",
    "cal_hour": "Час расчёта",
    "cal_season": "Сезон",
    "cal_month": "Месяц",
    "cal_day_of_year": "День года",
    "cal_day_of_week": "День недели",
    "cal_is_day_off": "Нерабочий день",
    "cal_is_holiday": "Праздник",
    "cal_day_off_chain": "Длина цепочки нерабочих дней",
}

_MODEL_MISSING = (
    "модель направления FLOOD_RISK не найдена ни в реестре, ни в ARTIFACTS_DIR; "
    "прогнозов по направлению нет, пока модель не обучена"
)
_FACTOR_WEIGHT_FLOOR = 0.02
_FACTOR_NOTE = (
    "Полосы показывают силу и направление фактора, а не слагаемые "
    "вероятности: вклады SHAP складываются в логарифме шансов, а не в ней."
)

_CACHE: dict[str, Any] = {}


def _model() -> Any | None:
    """Модель читается с диска один раз на процесс, как у доступа (ADR 0003)."""
    model = _CACHE.get("model")
    if model is None:
        model = load_model(DIRECTION)
        if model is not None:
            _CACHE["model"] = model
    return model


def _explainer(model: Any) -> Any:
    explainer = _CACHE.get("explainer")
    if explainer is None:
        import shap

        explainer = shap.TreeExplainer(model)
        _CACHE["explainer"] = explainer
    return explainer


def reset_cache() -> None:
    """Забывает модель и объяснитель. Нужен тесту, который подменяет модель."""
    _CACHE.clear()


def _row(model: Any, features: FeatureVector) -> list[list[float]]:
    return [[features[name] for name in model.feature_name()]]


def factors_block(
    names: Sequence[str], contributions: Sequence[float], features: FeatureVector
) -> Block:
    """Блок `factors` по правилу ADR 0011: слабые веса отсеиваются, первые три
    строки остаются всегда."""
    ranked = sorted(zip(names, contributions, strict=True), key=lambda pair: -abs(pair[1]))
    items: list[dict[str, Any]] = [
        {
            "label": FEATURE_LABELS.get(name, name),
            "weight": round(shap_weight(float(value)), 6),
            "value": f"{features.get(name, 0.0):g}",
        }
        for name, value in ranked
    ]
    kept = [
        item
        for index, item in enumerate(items)
        if index < 3 or abs(item["weight"]) >= _FACTOR_WEIGHT_FLOOR
    ]
    return Block(
        type="factors",
        title="Почему модель так решила",
        data={"items": kept, "note": _FACTOR_NOTE},
    )


def _rate(features: FeatureVector, at: datetime, column: str, windows: Sequence[int]) -> list:
    """Средняя частота по непересекающимся окнам, в сутки.

    Вектор держит вложенные окна. Разность соседних даёт непересекающееся
    окно, деление на его длину даёт частоту в сутки.
    """
    points = []
    edges = [*windows, 0]
    for start, end in zip(edges, edges[1:], strict=False):
        older = float(features.get(column.format(hours=start), 0.0))
        newer = float(features.get(column.format(hours=end), 0.0)) if end else 0.0
        days = (start - end) / 24.0
        middle = at - timedelta(hours=(start + end) / 2.0)
        points.append({"t": middle.isoformat(), "v": round(max(0.0, older - newer) / days, 3)})
    return points


def timeseries_block(features: FeatureVector, at: datetime) -> Block:
    return Block(
        type="timeseries",
        title="Затопления и работа насоса по окнам наблюдения",
        data={
            "series": [
                {
                    "name": "Часы с затоплением",
                    "unit": "часов в сутки",
                    "points": _rate(features, at, "evt_hours_{hours}h", (8760, 2160, 720, 168, 24)),
                },
                {
                    "name": "Переключения насоса",
                    "unit": "раз в сутки",
                    "points": _rate(features, at, "pump_changes_{hours}h", (24, 6, 1)),
                },
            ],
            "markerAt": at.isoformat(),
        },
    )


def timeline_block(features: FeatureVector, at: datetime) -> Block:
    events: list[dict[str, Any]] = []
    if features.get("evt_hours_8760h", 0.0) > 0:
        hours = float(features.get("evt_hours_since_last", 0.0))
        events.append(
            {
                "at": (at - timedelta(hours=hours)).isoformat(),
                "title": "Последнее затопление на пикете",
                "kind": "alarm",
                "note": (
                    f"За неделю часов с затоплением {features.get('evt_hours_168h', 0.0):g}, "
                    f"за год {features.get('evt_hours_8760h', 0.0):g}"
                ),
            }
        )
    if features.get("pump_blink_hours_24h", 0.0) > 0:
        events.append(
            {
                "at": at.isoformat(),
                "title": "Насос мигал в последние сутки",
                "kind": "alarm",
                "note": (
                    f"Переключений за сутки {features.get('pump_changes_24h', 0.0):g}. "
                    "Исправный насос не мигает"
                ),
            }
        )
    if features.get("obj_pump_blink_24h", 0.0) > 0 or features.get("obj_events_24h", 0.0) > 0:
        events.append(
            {
                "at": at.isoformat(),
                "title": "Беда у соседа по объекту",
                "kind": "alarm",
                "note": (
                    "Вода уходит по уклону: затопление или мигающий насос соседа "
                    "предвещает подтопление здесь"
                ),
            }
        )
    if features.get("pump_unavailable_24h", 0.0) > 0:
        events.append(
            {
                "at": at.isoformat(),
                "title": "Насос обесточен или неисправен",
                "kind": "alarm",
                "note": "Откачка на пикете под вопросом",
            }
        )
    events.append(
        {
            "at": at.isoformat(),
            "title": "Прогноз рассчитан",
            "kind": "forecast",
            "note": "Признаки построены на начало часа",
        }
    )
    events.sort(key=lambda event: str(event["at"]), reverse=True)
    return Block(type="timeline", title="События пикета", data={"events": events})


class FloodRisk:
    """Предиктор направления. Спецификация §7, `docs/08-ml-plugin.md`."""

    code = DIRECTION

    def applies(self, conn: Connection, facility_id: str) -> bool:
        """Отвечает, стоит ли на объекте насос или датчик затопления."""
        row = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM sensor
                    WHERE facility_id = :facility_id AND sensor_type IN (:pump, :flood)
                ) OR EXISTS (
                    SELECT 1 FROM alarm_event
                    WHERE facility_id = :facility_id AND alarm_type = ANY(:types)
                )
                """
            ),
            {
                "facility_id": facility_id,
                "pump": SENSOR_PUMP,
                "flood": SENSOR_FLOOD,
                "types": [*LABEL_TYPES, *STATE_TYPES],
            },
        ).scalar()
        return bool(row)

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        from app.features import registry

        model = _model()
        if model is None:
            return {}
        return registry.build(
            DIRECTION, ctx.conn, ctx.facility_id, ctx.at, list(model.feature_name())
        )

    def predict(self, features: FeatureVector) -> float:
        """Сырая вероятность бустера. Калибровки нет, как у доступа (ADR 0009)."""
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)
        return float(model.predict(_row(model, features))[0])

    def explain(self, features: FeatureVector, at: datetime | None = None) -> list[Block]:
        model = _model()
        if model is None:
            raise RuntimeError(_MODEL_MISSING)

        import numpy as np

        moment = at if at is not None else datetime.now(UTC).replace(microsecond=0)
        # SHAP предупреждает, что для двоичного LightGBM форма вывода сменилась
        # на список матриц. Обе формы ниже разобраны, поэтому предупреждение
        # глушится здесь, и только оно.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="LightGBM binary classifier")
            raw = _explainer(model).shap_values(np.asarray(_row(model, features), dtype=float))
        if isinstance(raw, list):
            raw = raw[-1]
        contributions = [float(value) for value in raw[0]]
        return [
            factors_block(model.feature_name(), contributions, features),
            timeseries_block(features, moment),
            timeline_block(features, moment),
        ]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        """Тип работ по сигналу, а не по вероятности.

        Идущее затопление требует откачки. Мигающий или недоступный насос
        требует проверки приямка и насоса. Иначе риск накопленный, и работа
        плановая: гидроизоляция.
        """
        if features.get("evt_hours_24h", 0.0) > 0:
            return "Откачка воды"
        if (
            features.get("pump_blink_hours_24h", 0.0) > 0
            or features.get("pump_unavailable_24h", 0.0) > 0
        ):
            return "Проверка приямка и насоса"
        return "Гидроизоляция"

    def label_rule(self, conn: Connection, facility_id: str, window: Window) -> bool:
        """Метка обучения (ADR 0010): «Затоплен» насоса или «Не замкнут» датчика
        затопления вне рабочего окна. Окно открыто слева: момент расчёта в метку
        не входит."""
        row = conn.execute(
            text(
                "SELECT 1 FROM alarm_event e WHERE e.facility_id = :facility_id "
                f"AND e.alarm_type = ANY(:label) AND {WATER} "
                "AND e.occurred_at > :start AND e.occurred_at <= :end LIMIT 1"
            ),
            {
                "facility_id": facility_id,
                "label": list(LABEL_TYPES),
                "holidays": holidays(),
                "start": window.start,
                "end": window.end,
            },
        ).fetchone()
        return row is not None


register(FloodRisk())
