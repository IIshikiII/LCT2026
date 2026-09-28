"""Перевод строки журнала СМВУ в событие или показание схемы. ADR 0017.

Журнал пишет тексты: тип канала из справочника и значение датчика. Схема
держит коды событий, которые читают признаки направлений. Правило одно на
загрузчик выгрузки (`app/ingest/dataset.py`) и на приём потока
(`app/ingest/stream.py`): иначе история и поток разошлись бы.

Правила повторяют витрины `ml/`:

- доступ: `ml/access/data.py`, пара «значение и тип датчика», тревожный флаг;
- подтопление: `ml/flood/01_dataset.py` и `03_pumps.py`, метка с тревожным
  флагом, состояния насоса без него;
- пожар: `ml/fire/01_dataset.py`, метка с тревожным флагом, «Обесточен» у
  фазы и числа температуры и метана без него.

Число вне шкалы датчика это неисправность канала, а не показание (ответ 1.7):
327,68, −100, 255, отрицательный газ. Такое значение в показания не идёт.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.features.access import CONTACT, MOTION, SECURITY_ARMED, SECURITY_DISARMED
from app.features.fire import (
    HEAT_OPEN,
    METRIC_METHANE,
    METRIC_TEMPERATURE,
    PHASE_OFF,
    SENSOR_HEAT,
    SENSOR_METHANE,
    SENSOR_SMOKE,
    SENSOR_TEMPERATURE,
    SMOKE_DETECTED,
    TEMP_HIGH,
)
from app.features.flood import (
    FLOOD_SENSOR_OPEN,
    FLOODED,
    PUMP_ALL_RUNNING,
    PUMP_OFF,
    PUMP_ON,
    PUMP_UNAVAILABLE,
    SENSOR_FLOOD,
    SENSOR_PUMP,
)

CONTACT_STYPES = ("КД АВ", "КД Дверь", "КД Люк", "9-секционный люк", "Стекло")
MOTION_STYPE = "Датчик движения"
GUARD_STYPE = "Состояние охраны"
PUMP_STYPE = "Состояние насоса"
FLOOD_STYPE = "Датчик затопления"
SMOKE_STYPE = "Датчик дыма"
HEAT_STYPE = "Тепловой датчик"
TEMP_STYPE = "Датчик температуры"
GAS_STYPE = "Газовый датчик"
PHASE_STYPE = "Состояние фазы"

# Каналы доступа живут на участке, остальные на пикете. ADR 0001, ADR 0017.
ACCESS_STYPES = (*CONTACT_STYPES, MOTION_STYPE)

# Код датчика в `sensor.sensor_type`. Признаки читают только эти коды.
SENSOR_TYPES: dict[str, str] = {
    **dict.fromkeys(CONTACT_STYPES, "CONTACT"),
    MOTION_STYPE: "MOTION",
    GUARD_STYPE: "GUARD",
    PUMP_STYPE: SENSOR_PUMP,
    FLOOD_STYPE: SENSOR_FLOOD,
    SMOKE_STYPE: SENSOR_SMOKE,
    HEAT_STYPE: SENSOR_HEAT,
    TEMP_STYPE: SENSOR_TEMPERATURE,
    GAS_STYPE: SENSOR_METHANE,
    PHASE_STYPE: "PHASE",
}


@dataclass(frozen=True)
class EventRule:
    """Строка журнала становится событием `code`, когда тип канала и значение
    совпали, а флаг «тревожное» стоит, если правило его требует."""

    stypes: tuple[str, ...]
    values: tuple[str, ...]
    needs_alarm: bool
    code: str


EVENT_RULES: tuple[EventRule, ...] = (
    EventRule(CONTACT_STYPES, ("Не замкнут",), True, CONTACT),
    EventRule((MOTION_STYPE,), ("Обнаружено движение",), True, MOTION),
    EventRule((GUARD_STYPE,), ("На охране",), False, SECURITY_ARMED),
    EventRule((GUARD_STYPE,), ("Снято с охраны",), False, SECURITY_DISARMED),
    EventRule((PUMP_STYPE,), ("Затоплен",), True, FLOODED),
    EventRule((FLOOD_STYPE,), ("Не замкнут",), True, FLOOD_SENSOR_OPEN),
    EventRule((PUMP_STYPE,), ("Включен",), False, PUMP_ON),
    EventRule((PUMP_STYPE,), ("Выключен",), False, PUMP_OFF),
    EventRule((PUMP_STYPE,), ("Работают все насосы в АНС",), False, PUMP_ALL_RUNNING),
    EventRule(
        (PUMP_STYPE,), ("Обесточен", "Неисправен", "Отключено устройство"), False, PUMP_UNAVAILABLE
    ),
    EventRule((SMOKE_STYPE,), ("Обнаружен дым",), True, SMOKE_DETECTED),
    EventRule((TEMP_STYPE,), ("Температура выше 40ºC",), True, TEMP_HIGH),
    EventRule((HEAT_STYPE,), ("Не замкнут",), True, HEAT_OPEN),
    EventRule((PHASE_STYPE,), ("Обесточен",), False, PHASE_OFF),
)


@dataclass(frozen=True)
class Numeric:
    """Числовой канал: метрика, единица и шкала датчика."""

    metric: str
    unit: str
    low: float
    high: float


NUMERIC: dict[str, Numeric] = {
    TEMP_STYPE: Numeric(METRIC_TEMPERATURE, "°C", -60.0, 150.0),
    GAS_STYPE: Numeric(METRIC_METHANE, "%", 0.0, 100.0),
}
# Значения-заглушки прошивки: неисправность, а не показание (ответ 1.7).
BROKEN_VALUES = (327.68, -100.0, 255.0)

_EVENTS: dict[tuple[str, str], EventRule] = {
    (stype, value): rule for rule in EVENT_RULES for stype in rule.stypes for value in rule.values
}


def event_code(stype: str | None, value: str | None, alarm: bool) -> str | None:
    """Код события строки журнала. Пусто, если строка не событие."""
    if stype is None or value is None:
        return None
    rule = _EVENTS.get((stype, value))
    if rule is None or (rule.needs_alarm and not alarm):
        return None
    return rule.code


def reading(stype: str | None, value: str | None) -> tuple[Numeric, float] | None:
    """Показание числового канала. Пусто, если канал не числовой или число битое."""
    spec = NUMERIC.get(stype or "")
    if spec is None or value is None:
        return None
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        return None
    if math.isnan(number) or number in BROKEN_VALUES or not spec.low <= number <= spec.high:
        return None
    return spec, number


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def event_code_sql(stype: str = "stype", value: str = "value", alarm: str = "alarm") -> str:
    """То же правило выражением `CASE` для DuckDB: загрузчик судит им миллионы
    строк, не поднимая их в Python."""
    parts = []
    for rule in EVENT_RULES:
        stypes = ", ".join(_quote(s) for s in rule.stypes)
        values = ", ".join(_quote(v) for v in rule.values)
        cond = f"{stype} IN ({stypes}) AND {value} IN ({values})"
        if rule.needs_alarm:
            cond += f" AND {alarm}"
        parts.append(f"WHEN {cond} THEN {_quote(rule.code)}")
    return "CASE " + " ".join(parts) + " END"


def reading_sql(stype: str = "stype", value: str = "value") -> tuple[str, str]:
    """Выражения метрики и числа для DuckDB. Битое число даёт NULL."""
    metric = (
        "CASE "
        + " ".join(
            f"WHEN {stype} = {_quote(k)} THEN {_quote(v.metric)}" for k, v in NUMERIC.items()
        )
        + " END"
    )
    number = f"TRY_CAST(replace({value}, ',', '.') AS DOUBLE)"
    broken = ", ".join(str(v) for v in BROKEN_VALUES)
    bounds = " ".join(
        f"WHEN {stype} = {_quote(k)} AND {number} BETWEEN {v.low} AND {v.high} "
        f"AND {number} NOT IN ({broken}) THEN {number}"
        for k, v in NUMERIC.items()
    )
    return metric, f"CASE {bounds} END"
