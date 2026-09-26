"""Часы заглушки СМВУ. ADR 0017.

Заглушка продолжает сдвинутую историю с той же точки недели и крутит неделю
по кругу. День недели и время суток сохраняются на каждом круге.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.pipeline.schedule import next_tick
from app.stub.smvu import Clock, Row, as_event, first_index

META = {"slice_start": "2026-03-02T00:00:00", "slice_days": 7, "shift_days": 203}


def test_the_clock_starts_at_the_shifted_monday_in_moscow() -> None:
    clock = Clock.from_meta(META)
    # 2026-03-02 + 203 суток = 2026-09-21, понедельник; полночь Москвы это 21:00 UTC.
    assert clock.origin == datetime(2026, 9, 20, 21, 0, tzinfo=UTC)
    assert clock.origin.astimezone(UTC).weekday() == 6


def test_the_position_wraps_the_week() -> None:
    clock = Clock.from_meta(META)
    now = clock.origin + timedelta(days=9, hours=2)

    loop, offset = clock.position(now)

    assert loop == 1
    assert offset == timedelta(days=2, hours=2).total_seconds()
    assert clock.moment(loop, offset) == now


def test_a_row_keeps_its_weekday_and_hour_on_every_loop() -> None:
    clock = Clock.from_meta(META)
    row = Row(
        offset_s=timedelta(days=3, hours=14, minutes=5).total_seconds(),
        event_id="42",
        channel_id="120578",
        alarm="true",
        value="Не замкнут",
    )

    first = as_event(row, clock.moment(0, row.offset_s))
    third = as_event(row, clock.moment(2, row.offset_s))

    assert first["время"] == third["время"] == "14:05:00"
    assert first["дата"] == "2026-09-24"
    assert third["дата"] == "2026-10-08"
    assert first["ид_канала_данных"] == 120578


def test_the_first_row_not_before_the_offset() -> None:
    rows = [Row(float(s), "", "1", "false", "0") for s in (0, 10, 10, 20)]
    assert first_index(rows, 0) == 0
    assert first_index(rows, 10) == 1
    assert first_index(rows, 11) == 3
    assert first_index(rows, 99) == 4


def test_the_schedule_ticks_on_whole_steps() -> None:
    assert next_tick(120.0, 60) == 180
    assert next_tick(121.5, 60) == 180
    assert next_tick(179.9, 60) == 180
