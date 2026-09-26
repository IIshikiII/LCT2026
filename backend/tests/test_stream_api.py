"""Приём потока СМВУ. ADR 0017.

Ручка принимает строки журнала под своим ключом. Канал находит датчик,
значение становится событием по правилу загрузчика, число ложится в час.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.api import stream as stream_module
from app.config import config
from app.db import engine
from app.main import app
from app.tables import alarm_event, facility, sensor, sensor_reading
from tests.conftest import reset_database

client = TestClient(app)
KEY = "stream-key"
URL = "/api/v1/stream/events"


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
            facility.insert(),
            [
                {
                    "id": fid,
                    "collector": "20",
                    "district": "объект Альфа",
                    "address": fid,
                    "lat": 55.75,
                    "lon": 37.61,
                    "facility_type": kind,
                    "kind": kind,
                }
                for fid, kind in (
                    ("P-20-G1-87", "picket"),
                    ("S-20-G1-1", "section"),
                    ("S-20-G1-2", "section"),
                )
            ],
        )
        conn.execute(
            sensor.insert(),
            [
                {
                    "id": "C101",
                    "facility_id": "P-20-G1-87",
                    "sensor_type": "SMOKE",
                    "channel_id": 101,
                    "channel_type": "Датчик дыма",
                },
                {
                    "id": "C102",
                    "facility_id": "P-20-G1-87",
                    "sensor_type": "METHANE",
                    "channel_id": 102,
                    "channel_type": "Газовый датчик",
                },
                {
                    "id": "C103-S-20-G1-1",
                    "facility_id": "S-20-G1-1",
                    "sensor_type": "GUARD",
                    "channel_id": 103,
                    "channel_type": "Состояние охраны",
                },
                {
                    "id": "C103-S-20-G1-2",
                    "facility_id": "S-20-G1-2",
                    "sensor_type": "GUARD",
                    "channel_id": 103,
                    "channel_type": "Состояние охраны",
                },
            ],
        )
    yield
    with engine().begin() as conn:
        reset_database(conn)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stream_module, "config", replace(config, stream_token=KEY))


def _row(channel: int, clock: str, alarm: str, value: str) -> dict[str, object]:
    return {
        "ид_события": 1,
        "ид_канала_данных": channel,
        "дата": "2026-09-26",
        "время": clock,
        "тревожное": alarm,
        "значение_датчика": value,
    }


def test_the_stream_is_off_without_a_key(db: None) -> None:
    response = client.post(URL, json={"events": []})
    assert response.status_code == 404


def test_a_wrong_key_is_refused(db: None, enabled: None) -> None:
    response = client.post(URL, json={"events": []}, headers={"X-Stream-Token": "wrong"})
    assert response.status_code == 403


def test_the_stream_writes_events_and_hourly_readings(db: None, enabled: None) -> None:
    batch = [
        _row(101, "14:05:00", "true", "Обнаружен дым"),
        _row(101, "14:06:00", "false", "Дыма нет"),
        _row(102, "14:10:00", "false", "0.4"),
        _row(102, "14:40:00", "false", "1,2"),
        _row(102, "14:50:00", "false", "327.68"),
        _row(103, "08:00:00", "false", "Снято с охраны"),
        _row(999, "14:00:00", "true", "Обнаружен дым"),
    ]

    response = client.post(URL, json={"events": batch}, headers={"X-Stream-Token": KEY})

    assert response.status_code == 200
    body = response.json()
    assert body == {"received": 7, "events": 3, "readings": 1, "skipped": 3}
    with engine().connect() as conn:
        events = list(conn.execute(select(alarm_event).order_by(alarm_event.c.facility_id)))
        readings = list(conn.execute(select(sensor_reading)))
    assert {(e.facility_id, e.alarm_type) for e in events} == {
        ("P-20-G1-87", "SMOKE_DETECTED"),
        ("S-20-G1-1", "SECURITY_DISARMED"),
        ("S-20-G1-2", "SECURITY_DISARMED"),
    }
    assert all(e.received_at is not None for e in events)
    smoke = next(e for e in events if e.alarm_type == "SMOKE_DETECTED")
    assert smoke.occurred_at.isoformat() == "2026-09-26T11:05:00+00:00"
    assert len(readings) == 1
    assert readings[0].value == 1.2
    assert readings[0].observed_at.isoformat() == "2026-09-26T11:00:00+00:00"


def test_a_later_reading_of_the_hour_keeps_the_maximum(db: None, enabled: None) -> None:
    headers = {"X-Stream-Token": KEY}
    client.post(URL, json={"events": [_row(102, "14:10:00", "false", "2.5")]}, headers=headers)
    client.post(URL, json={"events": [_row(102, "14:20:00", "false", "0.1")]}, headers=headers)
    client.post(URL, json={"events": [_row(102, "14:30:00", "false", "3.0")]}, headers=headers)

    with engine().connect() as conn:
        values = [r.value for r in conn.execute(select(sensor_reading))]
    assert values == [3.0]
