"""Перевод строки журнала в событие и разбор пикета. ADR 0017.

Правила обязаны совпадать с витринами `ml/`: по ним обучены модели и
размечена история пожара.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.ingest import mapping
from app.ingest.channels import MAIN_LINE, Place, parse_place
from app.ingest.dataset import Plan


@pytest.mark.parametrize(
    ("stype", "value", "alarm", "code"),
    [
        ("КД АВ", "Не замкнут", True, "DOOR_OPEN"),
        ("КД АВ", "Не замкнут", False, None),
        ("Датчик движения", "Обнаружено движение", True, "MOTION"),
        ("Состояние охраны", "Снято с охраны", False, "SECURITY_DISARMED"),
        ("Состояние охраны", "На охране", False, "SECURITY_ARMED"),
        ("Состояние насоса", "Затоплен", True, "FLOODED"),
        ("Состояние насоса", "Включен", False, "PUMP_ON"),
        ("Состояние насоса", "Неисправен", False, "PUMP_UNAVAILABLE"),
        ("Датчик затопления", "Не замкнут", True, "FLOOD_SENSOR_OPEN"),
        ("Датчик дыма", "Обнаружен дым", True, "SMOKE_DETECTED"),
        ("Датчик дыма", "Обнаружен дым", False, None),
        ("Тепловой датчик", "Не замкнут", True, "HEAT_OPEN"),
        ("Датчик температуры", "Температура выше 40ºC", True, "TEMP_HIGH"),
        ("Состояние фазы", "Обесточен", False, "PHASE_OFF"),
        ("Тепловой датчик", "Норма", False, None),
        (None, "Не замкнут", True, None),
    ],
)
def test_the_event_rule_pairs_the_channel_type_with_the_value(
    stype: str | None, value: str, alarm: bool, code: str | None
) -> None:
    assert mapping.event_code(stype, value, alarm) == code


def test_a_contact_value_on_another_channel_type_is_not_access() -> None:
    """«Не замкнут» пишут одиннадцать типов, и доступ это только контакты входа."""
    assert mapping.event_code("Датчик затопления", "Не замкнут", True) != "DOOR_OPEN"


@pytest.mark.parametrize(
    ("stype", "value", "expected"),
    [
        ("Датчик температуры", "28", 28.0),
        ("Газовый датчик", "0,02", 0.02),
        ("Газовый датчик", "-0.1", None),
        ("Газовый датчик", "327.68", None),
        ("Датчик температуры", "-100", None),
        ("Датчик температуры", "255", None),
        ("Датчик температуры", "Норма", None),
        ("Датчик дыма", "28", None),
    ],
)
def test_a_broken_number_is_not_a_reading(stype: str, value: str, expected: float | None) -> None:
    result = mapping.reading(stype, value)
    assert (result[1] if result else None) == expected


@pytest.mark.parametrize(
    ("name", "place"),
    [
        ("ТД ПК87", Place(87, 0.0, MAIN_LINE)),
        ("ДД ПК1+0,5", Place(1, 0.5, MAIN_LINE)),
        ("ТД ПК290-292", Place(290, 0.0, MAIN_LINE)),
        ("Темп. ВШ ПК88,5", Place(88, 0.0, MAIN_LINE)),
        ("Темп. ПК399 Г0ПК5", Place(5, 0.0, 0)),
        ("Шкаф ОПС", None),
    ],
)
def test_the_picket_forms_of_the_channel_name(name: str, place: Place | None) -> None:
    assert parse_place(name) == place


def test_the_shift_is_whole_weeks_and_the_history_ends_now() -> None:
    start = datetime(2026, 3, 2)  # понедельник
    now = datetime(2026, 9, 26, 16, 30)

    plan = Plan.for_now(start, now)

    assert plan.shift.days % 7 == 0
    assert start <= plan.cutoff < start.replace(day=9)
    assert plan.cutoff + plan.shift == now
    assert (start + plan.shift).weekday() == start.weekday()
