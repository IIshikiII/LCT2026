"""Погода недели потока по кругу. ADR 0018."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.ingest import weather_loop
from app.tables import ingest_state, weather_hourly
from tests.conftest import reset_database

# Неделя потока с понедельника 2026-03-09, сдвиг 196 суток: первый круг
# начинается в полночь 2026-09-21 по Москве, это 21:00 UTC 20 сентября.
ORIGIN = datetime(2026, 9, 20, 21, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[None]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")
    migrate.run()
    hours = [
        {
            "temperature_c": float(h),
            "humidity": None,
            "precip_mm": 0.0,
            "rain_mm": 0.0,
            "snowfall_cm": 0.0,
            "snow_depth_m": 0.0,
        }
        for h in range(168)
    ]
    with engine().begin() as conn:
        reset_database(conn)
        conn.execute(
            ingest_state.insert().values(
                key=weather_loop.STATE_KEY,
                value={"slice_start": "2026-03-09T00:00:00", "shift_days": 196, "hours": hours},
                updated_at=datetime.now(UTC),
            )
        )
        conn.execute(
            weather_hourly.insert().values(
                observed_at=ORIGIN + timedelta(hours=100), district="MOSCOW", temperature_c=100.0
            )
        )
    yield
    with engine().begin() as conn:
        reset_database(conn)


def test_the_weather_goes_on_with_the_hour_of_the_week(db: None) -> None:
    at = ORIGIN + timedelta(days=7, hours=5, minutes=30)
    with engine().begin() as conn:
        added = weather_loop.extend(conn, at)
    with engine().connect() as conn:
        rows = dict(
            conn.execute(select(weather_hourly.c.observed_at, weather_hourly.c.temperature_c)).all()
        )
    assert added == 168 + 5 - 100
    assert rows[ORIGIN + timedelta(hours=101)] == 101.0
    # Второй круг: тот же час недели, та же погода.
    assert rows[ORIGIN + timedelta(days=7, hours=5)] == 5.0


def test_a_second_call_adds_nothing(db: None) -> None:
    at = ORIGIN + timedelta(hours=120)
    with engine().begin() as conn:
        weather_loop.extend(conn, at)
        assert weather_loop.extend(conn, at) == 0
