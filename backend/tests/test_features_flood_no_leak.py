"""Ни один признак подтопления не смотрит после начала часа расчёта.

Проверка идёт по всему реестру направления сразу. Данные лежат на трёх уровнях:
сама единица, сосед по коллектору (объект) и сосед по округу (комплекс). Момент
расчёта стоит внутри часа: признаки обязаны округлить его вниз и не увидеть
событие между началом часа и самим моментом.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.engine import Connection

from app.db import engine
from app.features import flood, registry
from app.tables import alarm_event, collector, facility, sensor
from tests.conftest import reset_database

DIRECTION = flood.DIRECTION
UNIT = "F-FLOOD"
NEIGHBOUR = "F-FLOOD-2"
OTHER_OBJECT = "F-FLOOD-3"
AT = datetime(2026, 3, 2, 0, 20, tzinfo=UTC)
HOUR = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)


def _blink(code: str, start: datetime, changes: int) -> list[tuple[str, str, datetime]]:
    """Серия переключений внутри одного часа: мигающий насос."""
    return [
        (code, flood.PUMP_ON if i % 2 == 0 else flood.PUMP_OFF, start + timedelta(seconds=i * 90))
        for i in range(changes)
    ]


PAST = [
    (UNIT, flood.FLOODED, HOUR - timedelta(days=20)),
    (UNIT, flood.FLOODED, HOUR - timedelta(hours=5)),
    (UNIT, flood.PUMP_ON, HOUR - timedelta(hours=3)),
    (UNIT, flood.PUMP_OFF, HOUR - timedelta(hours=2)),
    (UNIT, flood.PUMP_ON, HOUR - timedelta(minutes=30)),
    (UNIT, flood.PUMP_UNAVAILABLE, HOUR - timedelta(hours=8)),
    (UNIT, flood.PUMP_ALL_RUNNING, HOUR - timedelta(hours=9)),
    *_blink(UNIT, HOUR - timedelta(hours=12), 20),
    (NEIGHBOUR, flood.FLOOD_SENSOR_OPEN, HOUR - timedelta(hours=10)),
    *_blink(NEIGHBOUR, HOUR - timedelta(hours=6), 18),
    (OTHER_OBJECT, flood.FLOODED, HOUR - timedelta(days=3)),
    (OTHER_OBJECT, flood.PUMP_UNAVAILABLE, HOUR - timedelta(hours=4)),
]

# Всё на начале часа расчёта и позже, включая отрезок до самого момента `AT`.
FUTURE = [
    (UNIT, flood.FLOODED, HOUR),
    (UNIT, flood.PUMP_OFF, HOUR + timedelta(minutes=10)),
    (UNIT, flood.PUMP_UNAVAILABLE, HOUR + timedelta(minutes=15)),
    *_blink(UNIT, HOUR + timedelta(hours=1), 25),
    (NEIGHBOUR, flood.FLOODED, HOUR + timedelta(minutes=5)),
    (OTHER_OBJECT, flood.FLOODED, HOUR + timedelta(days=1)),
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
    with engine().connect() as conn:
        return registry.build(DIRECTION, conn, UNIT, AT, names)


@pytest.fixture
def names() -> list[str]:
    return sorted(registry.known(DIRECTION))


@pytest.fixture
def both(names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    try:
        with engine().connect() as probe:
            probe.execute(delete(alarm_event).where(alarm_event.c.facility_id == "___"))
    except Exception as error:  # noqa: BLE001 — база недоступна это пропуск, не падение
        pytest.skip(f"тестовая база недоступна: {error}")

    with engine().begin() as conn:
        reset_database(conn)
        _place(conn)
        _events(conn, PAST, first_id=1)
    past_only = _vector(names)

    with engine().begin() as conn:
        _events(conn, FUTURE, first_id=1000)
    with_future = _vector(names)

    with engine().begin() as conn:
        reset_database(conn)
    return past_only, with_future


def test_the_registry_knows_all_42_training_features(names: list[str]) -> None:
    assert len(names) == 42


def test_the_point_sees_the_past(both: tuple[dict[str, float], dict[str, float]]) -> None:
    """Контроль: иначе тест на утечку прошёл бы и на нулевых признаках."""
    past_only, _ = both
    assert past_only["evt_hours_24h"] == 1
    assert past_only["evt_hours_since_last"] == 5
    assert past_only["pump_blink_hours_24h"] == 1
    assert past_only["pump_unavailable_24h"] == 1
    assert past_only["pump_all_running_24h"] == 1
    assert past_only["pump_on_share_24h"] > 0
    assert past_only["unit_pumps"] == 2
    assert past_only["obj_events_24h"] == 1
    assert past_only["obj_pump_blink_24h"] == 1
    assert past_only["cx_events_168h"] == 1
    assert past_only["cx_pump_unavailable_24h"] == 1
    assert past_only["cal_hour"] == 0


def test_no_feature_reads_the_future(
    both: tuple[dict[str, float], dict[str, float]],
) -> None:
    past_only, with_future = both
    differs = [name for name, value in past_only.items() if with_future[name] != value]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"
