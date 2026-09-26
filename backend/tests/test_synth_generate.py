"""Генератор синтетики доступа: детерминизм и повторный посев без дублей."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.features import fire, flood
from app.features.access import ACCESS_ALARM_TYPES, SECURITY_ARMED, SECURITY_DISARMED
from app.synth.generate import generate
from app.tables import alarm_event, collector, facility
from tests.conftest import reset_database

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
VALID_ALARM_TYPES = (
    set(ACCESS_ALARM_TYPES)
    | {SECURITY_ARMED, SECURITY_DISARMED}
    | set(flood.ALL_TYPES)
    | {*fire.SIGNAL_TYPES, fire.PHASE_OFF}
)


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

    yield

    with engine().begin() as conn:
        reset_database(conn)


def test_generate_fills_the_three_tables(db: None) -> None:
    with engine().begin() as conn:
        result = generate(conn, seed=1, facility_count=12, now=NOW)

    assert result.facility_count == 12
    assert result.collector_count > 0
    assert result.alarm_event_count > 0

    with engine().connect() as conn:
        facility_rows = list(conn.execute(select(facility)))
        collector_rows = list(conn.execute(select(collector)))
        alarm_rows = list(conn.execute(select(alarm_event)))

    assert len(facility_rows) == 12
    assert len(collector_rows) == result.collector_count
    assert len(alarm_rows) == result.alarm_event_count
    assert {row.alarm_type for row in alarm_rows} <= VALID_ALARM_TYPES
    assert all(row.occurred_at < NOW for row in alarm_rows)


def test_the_same_seed_gives_the_same_shape(db: None) -> None:
    with engine().begin() as conn:
        first = generate(conn, seed=7, facility_count=10, now=NOW)
    with engine().begin() as conn:
        reset_database(conn)
        second = generate(conn, seed=7, facility_count=10, now=NOW)

    assert first == second


def test_reseeding_the_same_database_does_not_duplicate_rows(db: None) -> None:
    with engine().begin() as conn:
        generate(conn, seed=3, facility_count=8, now=NOW)
    with engine().begin() as conn:
        generate(conn, seed=3, facility_count=8, now=NOW)

    with engine().connect() as conn:
        facility_count = conn.execute(select(facility)).rowcount
        alarm_count = len(list(conn.execute(select(alarm_event))))

    assert facility_count == 8
    assert alarm_count > 0


def test_a_risky_facility_has_more_alarms_than_a_calm_one(db: None) -> None:
    with engine().begin() as conn:
        generate(conn, seed=11, facility_count=30, now=NOW)

    with engine().connect() as conn:
        counts = dict(
            conn.execute(
                select(alarm_event.c.facility_id, text("count(*)"))
                .where(alarm_event.c.alarm_type.in_(ACCESS_ALARM_TYPES))
                .group_by(alarm_event.c.facility_id)
            ).all()
        )

    assert max(counts.values()) > 3 * (sum(counts.values()) / len(counts))


def test_pumps_blink_before_the_flood(db: None) -> None:
    """Синтетика подтопления держит форму данных: мигание идёт до затопления."""
    with engine().begin() as conn:
        result = generate(conn, seed=5, facility_count=24, now=NOW)

    assert result.sensor_count > 0
    with engine().connect() as conn:
        kinds = {
            row[0]
            for row in conn.execute(
                select(alarm_event.c.alarm_type).where(
                    alarm_event.c.alarm_type.in_(flood.ALL_TYPES)
                )
            )
        }
    assert {flood.PUMP_ON, flood.PUMP_OFF, flood.FLOODED} <= kinds
