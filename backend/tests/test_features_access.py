"""Признаки направления доступа на участке: правило события и счёт окон.

Числа сверены с `ml/access/data.py` и `ml/access/panel.py` вручную: у каждого
случая записано, что дало бы обучение.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.features import access_daily, registry
from app.tables import alarm_event, facility, sensor
from tests.conftest import reset_database

DIRECTION = "UNAUTHORIZED_ACCESS"
# Понедельник, 00:00 по Москве.
AT = datetime(2026, 3, 1, 21, 0, tzinfo=UTC)
TRUSTED = "F-TRUSTED"
LONE = "F-LONE"
OTHER = "F-OTHER"


@pytest.fixture
def conn() -> Iterator[Connection]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as setup:
        reset_database(setup)
        for code, collector in ((TRUSTED, "K-1"), (LONE, "K-1"), (OTHER, "K-2")):
            setup.execute(
                facility.insert(),
                {
                    "id": code,
                    "collector": collector,
                    "district": "CAO",
                    "address": "тест",
                    "lat": 0.0,
                    "lon": 0.0,
                    "facility_type": "chamber",
                    "commissioned_at": date(2026, 1, 1),
                    "is_active": True,
                },
            )
        # Участок TRUSTED держат два контакта: одиночному движению на нём не верят.
        setup.execute(
            sensor.insert(),
            [
                {"id": f"{TRUSTED}-C{i}", "facility_id": TRUSTED, "sensor_type": "CONTACT"}
                for i in (1, 2)
            ],
        )
    access_daily.reset_cache()
    with engine().begin() as work:
        yield work
    access_daily.reset_cache()
    with engine().begin() as teardown:
        reset_database(teardown)


_ids = iter(range(1, 1_000_000))


def _alarm(conn: Connection, code: str, kind: str, ts: datetime, sensor_id: str = "") -> None:
    conn.execute(
        alarm_event.insert(),
        {
            "id": next(_ids),
            "facility_id": code,
            "sensor_id": sensor_id or f"{code}-{kind}",
            "alarm_type": kind,
            "occurred_at": ts,
        },
    )


def _vector(conn: Connection, code: str, names: list[str], at: datetime = AT) -> dict[str, float]:
    access_daily.reset_cache()
    return registry.build(DIRECTION, conn, code, at, names)


def test_single_motion_on_a_trusted_unit_is_not_an_event(conn: Connection) -> None:
    _alarm(conn, TRUSTED, "MOTION", AT - timedelta(hours=5))
    got = _vector(conn, TRUSTED, ["n_alarms_7d", "n_armed_7d"])
    assert got == {"n_alarms_7d": 1.0, "n_armed_7d": 0.0}


def test_single_motion_on_a_unit_with_one_contact_is_an_event(conn: Connection) -> None:
    _alarm(conn, LONE, "MOTION", AT - timedelta(hours=5))
    assert _vector(conn, LONE, ["n_armed_7d"]) == {"n_armed_7d": 1.0}


def test_contact_makes_the_whole_incident_an_event(conn: Connection) -> None:
    """Контакт и движение через 20 минут это один инцидент, оба момента в событии."""
    _alarm(conn, TRUSTED, "DOOR_OPEN", AT - timedelta(hours=5))
    _alarm(conn, TRUSTED, "MOTION", AT - timedelta(hours=4, minutes=40))
    assert _vector(conn, TRUSTED, ["n_armed_7d"]) == {"n_armed_7d": 2.0}


def test_motion_after_a_long_pause_starts_a_new_incident(conn: Connection) -> None:
    _alarm(conn, TRUSTED, "DOOR_OPEN", AT - timedelta(hours=5))
    _alarm(conn, TRUSTED, "MOTION", AT - timedelta(hours=4, minutes=20))
    assert _vector(conn, TRUSTED, ["n_armed_7d"]) == {"n_armed_7d": 1.0}


def test_two_motion_sensors_make_an_event(conn: Connection) -> None:
    _alarm(conn, TRUSTED, "MOTION", AT - timedelta(hours=5), f"{TRUSTED}-M1")
    _alarm(conn, TRUSTED, "MOTION", AT - timedelta(hours=4, minutes=50), f"{TRUSTED}-M2")
    assert _vector(conn, TRUSTED, ["n_armed_7d"]) == {"n_armed_7d": 2.0}


def test_alarm_inside_a_disarm_window_is_an_alarm_but_not_an_event(conn: Connection) -> None:
    _alarm(conn, LONE, "SECURITY_DISARMED", AT - timedelta(hours=6))
    _alarm(conn, LONE, "DOOR_OPEN", AT - timedelta(hours=5))
    _alarm(conn, LONE, "SECURITY_ARMED", AT - timedelta(hours=4))
    got = _vector(conn, LONE, ["n_alarms_7d", "n_armed_7d", "disarm_hours_7d"])
    assert got == {"n_alarms_7d": 1.0, "n_armed_7d": 0.0, "disarm_hours_7d": 2.0}


def test_a_burst_in_one_second_weighs_one_moment(conn: Connection) -> None:
    for _ in range(3):
        _alarm(conn, LONE, "DOOR_OPEN", AT - timedelta(hours=5))
    assert _vector(conn, LONE, ["n_alarms_7d"]) == {"n_alarms_7d": 1.0}


def test_recency_counts_from_the_start_of_the_hour(conn: Connection) -> None:
    """`panel.py` берёт час тревоги, а не минуту: 5 ч 20 мин назад это 6 часов."""
    _alarm(conn, LONE, "DOOR_OPEN", AT - timedelta(hours=5, minutes=20))
    got = _vector(conn, LONE, ["hours_since_last_armed", "days_since_last_armed"])
    assert got == {"hours_since_last_armed": 6.0, "days_since_last_armed": 1.0}


def test_unit_without_events_counts_its_age(conn: Connection) -> None:
    """Участок живёт с 1 января по Москве, до полуночи 2 марта 60 суток."""
    got = _vector(conn, OTHER, ["unit_age_days", "days_since_last_armed", "hours_since_last_armed"])
    assert got == {
        "unit_age_days": 60.0,
        "days_since_last_armed": 61.0,
        "hours_since_last_armed": 24.0 * 61,
    }


def test_event_share_counts_days_with_an_event(conn: Connection) -> None:
    for days in (1, 1, 3, 40):
        _alarm(conn, LONE, "DOOR_OPEN", AT - timedelta(days=days, hours=-2))
    got = _vector(conn, LONE, ["armed_day_share_30d"])
    assert got["armed_day_share_30d"] == pytest.approx(2 / 30)


def test_event_a_week_ago_lands_on_day_six(conn: Connection) -> None:
    _alarm(conn, LONE, "DOOR_OPEN", AT - timedelta(days=6, hours=12))
    assert _vector(conn, LONE, ["armed_same_weekday_1w"]) == {"armed_same_weekday_1w": 1.0}


def test_guard_change_is_empty_without_switches(conn: Connection) -> None:
    got = _vector(conn, LONE, ["hours_since_guard_change"])
    assert math.isnan(got["hours_since_guard_change"])


def test_guard_change_ignores_a_repeated_mode(conn: Connection) -> None:
    _alarm(conn, LONE, "SECURITY_DISARMED", AT - timedelta(hours=30))
    _alarm(conn, LONE, "SECURITY_ARMED", AT - timedelta(hours=10))
    _alarm(conn, LONE, "SECURITY_ARMED", AT - timedelta(hours=3))
    assert _vector(conn, LONE, ["hours_since_guard_change"]) == {"hours_since_guard_change": 10.0}


def test_object_counts_every_unit_day_with_an_alarm(conn: Connection) -> None:
    """Сумма по суткам числа участков с тревогой, а не число разных участков."""
    _alarm(conn, TRUSTED, "MOTION", AT - timedelta(hours=5))
    _alarm(conn, LONE, "MOTION", AT - timedelta(hours=5))
    _alarm(conn, LONE, "MOTION", AT - timedelta(days=2))
    _alarm(conn, OTHER, "MOTION", AT - timedelta(hours=5))
    got = _vector(conn, LONE, ["obj_alarms_1d", "obj_alarms_7d", "obj_units_alarmed_7d"])
    assert got == {"obj_alarms_1d": 2.0, "obj_alarms_7d": 3.0, "obj_units_alarmed_7d": 3.0}


def test_network_share_divides_by_living_units(conn: Connection) -> None:
    """Три живых участка, событие на одном за последние сутки."""
    _alarm(conn, LONE, "DOOR_OPEN", AT - timedelta(hours=5))
    got = _vector(conn, OTHER, ["net_event_share_1d", "net_event_share_7d"])
    assert got["net_event_share_1d"] == pytest.approx(1 / 3)
    assert got["net_event_share_7d"] == pytest.approx(1 / 21)


def test_same_date_share_reads_previous_years(conn: Connection) -> None:
    """Событие 2 марта 2025 года. Участок живёт с 1 января 2026 года, поэтому
    в 2025 году живых пар нет, и в знаменателе остаётся ноль."""
    _alarm(conn, LONE, "DOOR_OPEN", datetime(2025, 3, 2, 9, 0, tzinfo=UTC))
    got = _vector(conn, OTHER, ["date_event_share_prev_years"])
    assert math.isnan(got["date_event_share_prev_years"])

    conn.execute(text("UPDATE facility SET commissioned_at = '2024-12-01'"))
    got = _vector(conn, OTHER, ["date_event_share_prev_years"])
    # Семь дат 2025 года вокруг 2 марта, три участка: одна пара из 21.
    assert got["date_event_share_prev_years"] == pytest.approx(1 / 21)


def test_calendar_reads_the_moscow_date(conn: Connection) -> None:
    """Точка расчёта 21:00 UTC это полночь понедельника 2 марта по Москве."""
    got = _vector(conn, LONE, ["day_of_week", "day_of_month", "day_of_year", "is_day_off"])
    assert got == {"day_of_week": 0.0, "day_of_month": 2.0, "day_of_year": 61.0, "is_day_off": 0.0}
