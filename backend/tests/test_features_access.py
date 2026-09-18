"""Признаки направления доступа: правильный счёт и запрет на утечку."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.features.access import build_features
from app.ml.protocol import FeatureContext
from app.tables import alarm_event, facility

AT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[None]:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as conn:
        conn.execute(delete(alarm_event))
        conn.execute(delete(facility))

    yield

    with engine().begin() as conn:
        conn.execute(delete(alarm_event))
        conn.execute(delete(facility))


def _add_facility(conn: object, facility_id: str) -> None:
    conn.execute(  # type: ignore[attr-defined]
        facility.insert(),
        {
            "id": facility_id,
            "collector": "K-TEST",
            "district": "CAO",
            "address": "тест",
            "lat": 0.0,
            "lon": 0.0,
            "facility_type": "chamber",
            "is_active": True,
        },
    )


_next_alarm_id = iter(range(1, 100_000))


def _add_alarm(
    conn: object,
    facility_id: str,
    sensor_id: str,
    alarm_type: str,
    occurred_at: datetime,
) -> None:
    conn.execute(  # type: ignore[attr-defined]
        alarm_event.insert(),
        {
            "id": next(_next_alarm_id),
            "sensor_id": sensor_id,
            "facility_id": facility_id,
            "occurred_at": occurred_at,
            "alarm_type": alarm_type,
        },
    )


@pytest.mark.usefixtures("db")
def test_windowed_counts_and_shares() -> None:
    fid = "F-ACC-WINDOWS"
    events = [
        ("S1", "DOOR_OPEN", AT - timedelta(minutes=30)),
        ("S2", "VOLUMETRIC", AT - timedelta(hours=2)),
        ("S3", "MOTION", datetime(2026, 9, 6, 9, 0, 0, tzinfo=UTC)),
        ("S1", "DOOR_OPEN", datetime(2026, 8, 25, 9, 0, 0, tzinfo=UTC)),
        ("S1", "DOOR_OPEN", datetime(2025, 1, 1, 23, 0, 0, tzinfo=UTC)),
        # Событие после `at`: обязано остаться невидимым для каждого признака.
        ("S1", "DOOR_OPEN", AT + timedelta(minutes=10)),
    ]
    with engine().begin() as conn:
        _add_facility(conn, fid)
        for sensor_id, alarm_type, occurred_at in events:
            _add_alarm(conn, fid, sensor_id, alarm_type, occurred_at)

    with engine().connect() as conn:
        features = build_features(FeatureContext(conn=conn, facility_id=fid, at=AT))

    assert features["n_alarms_1h"] == 1.0
    assert features["n_alarms_24h"] == 2.0
    assert features["n_alarms_168h"] == 3.0
    assert features["n_alarms_720h"] == 4.0
    assert features["alarm_hour_share_720h"] == pytest.approx(4 / 720.0)
    assert features["hours_since_last_alarm"] == 0.0
    assert features["night_share"] == pytest.approx(0.2)
    assert features["neighbor_channels_1h"] == 1.0
    assert features["hour_of_day"] == 12.0
    assert features["is_weekend"] == 0.0
    assert features["day_of_week"] == float((AT.weekday() + 1) % 7)
    assert features["month"] == 9.0


@pytest.mark.usefixtures("db")
def test_is_disarmed_follows_latest_toggle() -> None:
    still_disarmed = "F-ACC-DISARMED"
    rearmed = "F-ACC-REARMED"
    with engine().begin() as conn:
        _add_facility(conn, still_disarmed)
        _add_facility(conn, rearmed)
        _add_alarm(conn, still_disarmed, "S1", "SECURITY_DISARMED", AT - timedelta(hours=3))
        _add_alarm(conn, rearmed, "S1", "SECURITY_DISARMED", AT - timedelta(hours=3))
        _add_alarm(conn, rearmed, "S1", "SECURITY_ARMED", AT - timedelta(hours=1))

    with engine().connect() as conn:
        disarmed_features = build_features(
            FeatureContext(conn=conn, facility_id=still_disarmed, at=AT)
        )
        rearmed_features = build_features(FeatureContext(conn=conn, facility_id=rearmed, at=AT))

    assert disarmed_features["is_disarmed"] == 1.0
    assert rearmed_features["is_disarmed"] == 0.0


@pytest.mark.usefixtures("db")
def test_armed_alarms_exclude_disarm_window() -> None:
    fid = "F-ACC-ARMED"
    with engine().begin() as conn:
        _add_facility(conn, fid)
        _add_alarm(conn, fid, "S1", "SECURITY_DISARMED", AT - timedelta(hours=10))
        _add_alarm(conn, fid, "S1", "SECURITY_ARMED", AT - timedelta(hours=5))
        # Внутри окна «снято с охраны»: не идёт в число тревог на охране.
        _add_alarm(conn, fid, "S2", "DOOR_OPEN", AT - timedelta(hours=8))
        # После повторной постановки: считается.
        _add_alarm(conn, fid, "S2", "DOOR_OPEN", AT - timedelta(hours=2))

    with engine().connect() as conn:
        features = build_features(FeatureContext(conn=conn, facility_id=fid, at=AT))

    assert features["n_armed_alarms_24h"] == 1.0
    assert features["n_armed_alarms_168h"] == 1.0
    assert features["hours_since_last_armed_alarm"] == 2.0


@pytest.mark.usefixtures("db")
def test_access_sequence_needs_all_three_steps_in_order() -> None:
    complete = "F-ACC-CHAIN-OK"
    partial = "F-ACC-CHAIN-MISSING-MOTION"
    with engine().begin() as conn:
        _add_facility(conn, complete)
        _add_facility(conn, partial)
        _add_alarm(conn, complete, "S1", "DOOR_OPEN", AT - timedelta(minutes=10))
        _add_alarm(conn, complete, "S2", "VOLUMETRIC", AT - timedelta(minutes=8))
        _add_alarm(conn, complete, "S3", "MOTION", AT - timedelta(minutes=6))
        _add_alarm(conn, partial, "S1", "DOOR_OPEN", AT - timedelta(minutes=10))
        _add_alarm(conn, partial, "S2", "VOLUMETRIC", AT - timedelta(minutes=8))

    with engine().connect() as conn:
        complete_features = build_features(FeatureContext(conn=conn, facility_id=complete, at=AT))
        partial_features = build_features(FeatureContext(conn=conn, facility_id=partial, at=AT))

    assert complete_features["has_access_sequence"] == 1.0
    assert partial_features["has_access_sequence"] == 0.0


@pytest.mark.usefixtures("db")
def test_facility_without_history_gets_zero_not_error() -> None:
    fid = "F-ACC-EMPTY"
    with engine().begin() as conn:
        _add_facility(conn, fid)

    with engine().connect() as conn:
        features = build_features(FeatureContext(conn=conn, facility_id=fid, at=AT))

    assert features["n_alarms_24h"] == 0.0
    assert features["hours_since_last_alarm"] == 0.0
    assert features["is_disarmed"] == 0.0
    assert features["has_access_sequence"] == 0.0
