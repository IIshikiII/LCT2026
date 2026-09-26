"""Пожар на участке и его живые блоки. ADR 0018.

Прогноз ставится на участок, датчики живут на его пикетах. Переход сигнала на
соседа считается по координате вдоль линии, не дальше 50 м. Карточка
перечисляет все датчики участка и отмечает, к какому очаг ближе.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.features import fire, fire_live
from app.ml.fire_rules import assess
from app.ml.plugins.fire_risk import FireRisk
from app.tables import collector, facility, sensor
from tests.conftest import reset_database

AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)  # среда, 15:00 по Москве


def _facility(fid: str, kind: str, parent: str | None, chainage: float | None) -> dict[str, object]:
    return {
        "id": fid,
        "collector": "20",
        "district": "комплекс",
        "address": fid,
        "lat": 55.75,
        "lon": 37.61,
        "facility_type": kind,
        "kind": kind,
        "gallery": -1,
        "chainage_m": chainage,
        "picket": chainage / 10 if chainage is not None else None,
        "parent_id": parent,
    }


@pytest.fixture
def db() -> Iterator[None]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")
    migrate.run()
    with engine().begin() as conn:
        reset_database(conn)
        conn.execute(
            collector.insert().values(code="20", label="объект", district="комплекс", line=[])
        )
        conn.execute(
            facility.insert(),
            [
                _facility("S-20-1", "section", None, None),
                _facility("P-20-10", "picket", "S-20-1", 100.0),
                _facility("P-20-13", "picket", "S-20-1", 130.0),
                _facility("P-20-40", "picket", "S-20-2", 400.0),
                _facility("S-20-2", "section", None, None),
            ],
        )
        conn.execute(
            sensor.insert(),
            [
                {
                    "id": f"C{i}",
                    "facility_id": fid,
                    "sensor_type": "SMOKE",
                    "installed_at": datetime(2020, 1, 1).date(),
                    "channel_name": f"ДД ПК{pk}",
                }
                for i, (fid, pk) in enumerate((("P-20-10", 10), ("P-20-13", 13), ("P-20-40", 40)))
            ],
        )
    yield
    with engine().begin() as conn:
        reset_database(conn)


def _smoke(sensor_id: str, fid: str, at: datetime) -> None:
    with engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO alarm_event (sensor_id, facility_id, occurred_at, alarm_type) "
                "VALUES (:s, :f, :t, 'SMOKE_DETECTED')"
            ),
            {"s": sensor_id, "f": fid, "t": at},
        )


def test_the_forecast_goes_to_the_section_not_to_the_picket(db: None) -> None:
    plugin = FireRisk()
    with engine().connect() as conn:
        assert plugin.applies(conn, "S-20-1")
        assert not plugin.applies(conn, "P-20-10")
        assert fire.members(conn, "S-20-1") == ["P-20-10", "P-20-13"]


def test_smoke_on_a_picket_30_m_away_is_a_spread(db: None) -> None:
    _smoke("C0", "P-20-10", AT - timedelta(hours=2))
    _smoke("C1", "P-20-13", AT - timedelta(hours=2) + timedelta(minutes=10))
    with engine().connect() as conn:
        facts = fire.episode(conn, "S-20-1", AT).facts
    assert facts.spread
    assert assess(facts).level == "HIGH"


def test_smoke_on_a_picket_300_m_away_is_not_a_spread(db: None) -> None:
    _smoke("C0", "P-20-10", AT - timedelta(hours=2))
    _smoke("C2", "P-20-40", AT - timedelta(hours=2) + timedelta(minutes=10))
    with engine().connect() as conn:
        facts = fire.episode(conn, "S-20-1", AT).facts
    assert facts.signal and not facts.spread


def test_the_card_lists_every_sensor_and_marks_the_focus(db: None) -> None:
    _smoke("C1", "P-20-13", AT - timedelta(hours=3))
    with engine().connect() as conn:
        block = fire_live.sensors_block(conn, "S-20-1", AT)
    assert block is not None
    rows = block.data["rows"]
    assert [r["sensor"] for r in rows] == ["ДД ПК13", "ДД ПК10"]
    assert rows[0]["focus"].startswith("★")
    assert rows[0]["signals"] == 1


def test_an_empty_series_is_left_out(db: None) -> None:
    with engine().connect() as conn:
        assert fire_live.series_block(conn, "S-20-1", AT) is None
    _smoke("C0", "P-20-10", AT - timedelta(hours=1))
    with engine().connect() as conn:
        block = fire_live.series_block(conn, "S-20-1", AT)
    assert block is not None
    assert all(any(p["v"] for p in s["points"]) for s in block.data["series"])


def test_the_spark_line_shows_empty_windows_as_dots() -> None:
    assert fire_live.spark([0, 0, 0]) == "···"
    assert fire_live.spark([0, 1, 4]) == "·▃█"
