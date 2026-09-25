"""Ни один признак подтопления не смотрит после точки расчёта.

Точка расчёта это полночь по Москве (ADR 0013). Момент `AT` стоит внутри
суток: признаки обязаны сдвинуться к полуночи и не увидеть событие между ней
и самим моментом.

Проверка идёт по всему реестру направления сразу, вместе с разметкой суток
«вода или проверка»: разметка считается заново после того, как в базу легло
будущее. Данные лежат на трёх уровнях: сама единица, сосед по коллектору
(объект) и сосед по округу (комплекс). Классификатора воды в тесте нет,
работает правило времени: вода это сигнал ночью или в нерабочий день.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.engine import Connection

from app.db import engine
from app.features import flood, flood_water, registry
from app.tables import alarm_event, collector, facility, sensor, weather_hourly
from tests.conftest import reset_database

DIRECTION = flood.DIRECTION
UNIT = "F-FLOOD"
NEIGHBOUR = "F-FLOOD-2"
OTHER_OBJECT = "F-FLOOD-3"
# Полночь 2 марта 2026 года по Москве. 1 марта это воскресенье.
POINT = datetime(2026, 3, 1, 21, 0, tzinfo=UTC)
AT = POINT + timedelta(hours=5, minutes=20)


def _blink(code: str, start: datetime, changes: int) -> list[tuple[str, str, datetime]]:
    """Серия переключений внутри одного часа: мигающий насос."""
    return [
        (code, flood.PUMP_ON if i % 2 == 0 else flood.PUMP_OFF, start + timedelta(seconds=i * 90))
        for i in range(changes)
    ]


PAST = [
    (UNIT, flood.FLOODED, POINT - timedelta(days=20)),
    # Воскресенье, 19:00 по Москве: нерабочий день, вода.
    (UNIT, flood.FLOODED, POINT - timedelta(hours=5)),
    # Пятница, 10:00 по Москве: плановая проверка, не вода.
    (UNIT, flood.FLOODED, datetime(2026, 2, 27, 7, 0, tzinfo=UTC)),
    (UNIT, flood.PUMP_ON, POINT - timedelta(hours=3)),
    (UNIT, flood.PUMP_OFF, POINT - timedelta(hours=2)),
    (UNIT, flood.PUMP_ON, POINT - timedelta(minutes=30)),
    (UNIT, flood.PUMP_UNAVAILABLE, POINT - timedelta(hours=8)),
    (UNIT, flood.PUMP_ALL_RUNNING, POINT - timedelta(hours=9)),
    *_blink(UNIT, POINT - timedelta(hours=12), 20),
    (NEIGHBOUR, flood.FLOOD_SENSOR_OPEN, POINT - timedelta(hours=10)),
    *_blink(NEIGHBOUR, POINT - timedelta(hours=6), 18),
    (OTHER_OBJECT, flood.FLOODED, POINT - timedelta(days=3)),
    (OTHER_OBJECT, flood.PUMP_UNAVAILABLE, POINT - timedelta(hours=4)),
]

# Всё в точке расчёта и позже, включая отрезок до самого момента `AT`.
FUTURE = [
    (UNIT, flood.FLOODED, POINT),
    (UNIT, flood.FLOODED, POINT + timedelta(hours=2)),
    (UNIT, flood.PUMP_OFF, POINT + timedelta(minutes=10)),
    (UNIT, flood.PUMP_UNAVAILABLE, POINT + timedelta(minutes=15)),
    *_blink(UNIT, POINT + timedelta(hours=1), 25),
    (NEIGHBOUR, flood.FLOODED, POINT + timedelta(minutes=5)),
    (OTHER_OBJECT, flood.FLOODED, POINT + timedelta(days=1)),
]


def _weather(start: datetime, hours: int, *, snow: float) -> list[dict[str, object]]:
    return [
        {
            "observed_at": start + timedelta(hours=i),
            "district": flood.WEATHER_AREA,
            "temperature_c": 5.0,
            "precip_mm": 1.0,
            "rain_mm": 1.0,
            "snowfall_cm": 0.0,
            "snow_depth_m": snow,
        }
        for i in range(hours)
    ]


def _place(conn: Connection) -> None:
    conn.execute(
        insert(collector),
        [
            {"code": "K-FLOOD", "label": "Коллектор", "district": "SAO", "line": []},
            {"code": "K-FLOOD-2", "label": "Коллектор 2", "district": "SAO", "line": []},
        ],
    )
    conn.execute(
        insert(facility),
        [
            {
                "id": code,
                "collector": collector_code,
                "district": "SAO",
                "address": f"Улица, {code}",
                "lat": 55.8,
                "lon": 37.5,
                "facility_type": "chamber",
                "is_active": True,
            }
            for code, collector_code in (
                (UNIT, "K-FLOOD"),
                (NEIGHBOUR, "K-FLOOD"),
                (OTHER_OBJECT, "K-FLOOD-2"),
            )
        ],
    )
    conn.execute(
        insert(sensor),
        [
            {"id": f"{UNIT}-P1", "facility_id": UNIT, "sensor_type": flood.SENSOR_PUMP},
            {"id": f"{UNIT}-P2", "facility_id": UNIT, "sensor_type": flood.SENSOR_PUMP},
            {"id": f"{NEIGHBOUR}-S", "facility_id": NEIGHBOUR, "sensor_type": flood.SENSOR_FLOOD},
        ],
    )
    # Покров 30 см за трое суток до точки, 10 см в последний час: стаяло 20 см.
    rows = _weather(POINT - timedelta(hours=200), 200, snow=0.3)
    for row in rows:
        if row["observed_at"] > POINT - timedelta(hours=72):
            row["snow_depth_m"] = 0.1
    conn.execute(insert(weather_hourly), rows)


def _events(conn: Connection, rows: list[tuple[str, str, datetime]], first_id: int) -> None:
    conn.execute(
        insert(alarm_event),
        [
            {
                "id": first_id + offset,
                "facility_id": code,
                "sensor_id": f"{code}-{'S' if kind == flood.FLOOD_SENSOR_OPEN else 'P1'}",
                "alarm_type": kind,
                "occurred_at": ts,
            }
            for offset, (code, kind, ts) in enumerate(rows)
        ],
    )


def _vector(names: list[str]) -> dict[str, float]:
    with engine().begin() as conn:
        flood_water.refresh(conn, AT)
    with engine().connect() as conn:
        plain = registry.build(DIRECTION, conn, UNIT, AT, names)
        # Кэш запросов на один вектор обязан давать те же числа.
        with flood.memo():
            cached = registry.build(DIRECTION, conn, UNIT, AT, names)
    assert cached == plain
    return plain


@pytest.fixture
def names() -> list[str]:
    return sorted(registry.known(DIRECTION))


@pytest.fixture
def both(
    names: list[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, float], dict[str, float]]:
    try:
        with engine().connect() as probe:
            probe.execute(delete(alarm_event).where(alarm_event.c.facility_id == "___"))
    except Exception as error:  # noqa: BLE001 — база недоступна это пропуск, не падение
        pytest.skip(f"тестовая база недоступна: {error}")
    monkeypatch.setattr(flood_water, "classifier", lambda: None)

    with engine().begin() as conn:
        reset_database(conn)
        _place(conn)
        _events(conn, PAST, first_id=1)
    past_only = _vector(names)

    with engine().begin() as conn:
        _events(conn, FUTURE, first_id=1000)
        conn.execute(insert(weather_hourly), _weather(POINT, 30, snow=0.9))
    with_future = _vector(names)

    with engine().begin() as conn:
        reset_database(conn)
    return past_only, with_future


def test_the_point_is_moscow_midnight() -> None:
    assert flood.point_of(AT) == POINT
    assert flood.point_of(POINT) == POINT
    assert flood.point_of(POINT - timedelta(seconds=1)) == POINT - timedelta(days=1)


def test_the_registry_knows_all_training_features(names: list[str]) -> None:
    """52 признака модели ADR 0012 и 5 календарных, которые она не читает."""
    assert len(names) == 57


def test_the_point_sees_the_past(both: tuple[dict[str, float], dict[str, float]]) -> None:
    """Контроль: иначе тест на утечку прошёл бы и на нулевых признаках."""
    past_only, _ = both
    assert past_only["evt_hours_24h"] == 1
    assert past_only["evt_hours_168h"] == 1, "плановая проверка попала в воду"
    assert past_only["check_hours_168h"] == 1
    assert past_only["recent_check_hours_24h"] == 0
    assert past_only["recent_evt_hours_6h"] == 1
    assert past_only["evt_hours_since_last"] == 5
    assert past_only["pump_blink_hours_24h"] == 1
    assert past_only["pump_unavailable_24h"] == 1
    assert past_only["pump_all_running_24h"] == 1
    assert past_only["pump_on_share_24h"] > 0
    assert past_only["recent_pump_on_share_6h"] > 0
    assert past_only["unit_pumps"] == 2
    assert past_only["obj_events_24h"] == 1
    assert past_only["obj_pump_blink_24h"] == 1
    assert past_only["cx_events_168h"] == 1
    assert past_only["cx_pump_unavailable_24h"] == 1
    assert past_only["weather_precip_24h"] == 24
    assert past_only["weather_temp_mean_24h"] == 5
    assert past_only["weather_snow_depth_cm"] == pytest.approx(10)
    assert past_only["weather_melt_72h_cm"] == pytest.approx(20)
    assert past_only["cal_month"] == 3


def test_no_feature_reads_the_future(
    both: tuple[dict[str, float], dict[str, float]],
) -> None:
    past_only, with_future = both
    differs = [name for name, value in past_only.items() if with_future[name] != value]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"
