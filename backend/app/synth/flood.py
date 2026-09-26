"""Синтетика направления «риск подтопления».

Генератор кладёт насосы и датчики затопления на часть объектов и пишет их
историю в `alarm_event` кодами из `app/features/flood.py`. Форма повторяет то,
что показали данные (ADR 0009):

- насос работает циклами: включился, через минуты выключился;
- перед затоплением насос мигает: десятки переключений за час;
- затопление держится несколько часов и часто совпадает с тем, что работают
  все насосы станции, а насос бывает обесточен.

Случайность своя, от `seed + 1`. Поток событий доступа от этого не меняется,
и прежние тесты генератора видят те же строки.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from app.features.flood import (
    FLOOD_SENSOR_OPEN,
    FLOODED,
    PUMP_ALL_RUNNING,
    PUMP_OFF,
    PUMP_ON,
    PUMP_UNAVAILABLE,
    SENSOR_FLOOD,
    SENSOR_PUMP,
)

# Насос стоит на каждом четвёртом объекте, как на станциях вдоль трассы.
PUMP_EVERY = 4
# Доля объектов с насосом, где затопления случаются часто.
WET_SHARE = 0.5
CYCLES_PER_DAY = (2.0, 6.0)
EPISODE_EVERY_DAYS = (6.0, 14.0)
BLINK_CHANGES = (18, 40)


def _cycles(
    rng: random.Random, facility_id: str, sensor_id: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rate = rng.uniform(*CYCLES_PER_DAY)
    cursor = start
    while True:
        cursor += timedelta(hours=rng.expovariate(rate / 24))
        stop = cursor + timedelta(minutes=rng.uniform(3, 40))
        if stop >= end:
            break
        rows.append(_row(facility_id, sensor_id, PUMP_ON, cursor))
        rows.append(_row(facility_id, sensor_id, PUMP_OFF, stop))
        cursor = stop
    return rows


def _blink(
    rng: random.Random, facility_id: str, sensor_id: str, at: datetime
) -> list[dict[str, Any]]:
    changes = rng.randint(*BLINK_CHANGES)
    step = 3600 / (changes + 1)
    return [
        _row(
            facility_id,
            sensor_id,
            PUMP_ON if i % 2 == 0 else PUMP_OFF,
            at + timedelta(seconds=step * (i + 1)),
        )
        for i in range(changes)
    ]


def _episode(
    rng: random.Random,
    facility_id: str,
    pump_id: str,
    flood_sensor_id: str | None,
    at: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Эпизод подтопления: мигание за часы до, затем затопление на часы."""
    rows = _blink(rng, facility_id, pump_id, at - timedelta(hours=rng.uniform(4, 18)))
    for hour in range(rng.randint(1, 6)):
        moment = at + timedelta(hours=hour, minutes=rng.uniform(0, 50))
        if moment >= end:
            break
        rows.append(_row(facility_id, pump_id, FLOODED, moment))
        if flood_sensor_id and rng.random() < 0.5:
            rows.append(_row(facility_id, flood_sensor_id, FLOOD_SENSOR_OPEN, moment))
        if rng.random() < 0.4:
            rows.append(_row(facility_id, pump_id, PUMP_ALL_RUNNING, moment))
        if rng.random() < 0.2:
            rows.append(_row(facility_id, pump_id, PUMP_UNAVAILABLE, moment))
    return [row for row in rows if row["occurred_at"] < end]


def _row(facility_id: str, sensor_id: str, kind: str, at: datetime) -> dict[str, Any]:
    return {
        "sensor_id": sensor_id,
        "facility_id": facility_id,
        "occurred_at": at,
        "alarm_type": kind,
    }


def flood_rows(
    seed: int, facilities: list[dict[str, Any]], now: datetime, history_days: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Отдаёт строки `sensor` и `alarm_event` для объектов с насосами."""
    rng = random.Random(seed + 1)
    start = now - timedelta(days=history_days)
    sensors: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for index, row in enumerate(facilities):
        if index % PUMP_EVERY:
            continue
        facility_id = str(row["id"])
        pump_id = f"{facility_id}-PUMP"
        sensors.append({"id": pump_id, "facility_id": facility_id, "sensor_type": SENSOR_PUMP})
        flood_sensor_id = None
        if rng.random() < 0.5:
            flood_sensor_id = f"{facility_id}-FLOOD"
            sensors.append(
                {"id": flood_sensor_id, "facility_id": facility_id, "sensor_type": SENSOR_FLOOD}
            )
        events.extend(_cycles(rng, facility_id, pump_id, start, now))

        wet = rng.random() < WET_SHARE
        cursor = start
        while wet:
            cursor += timedelta(days=rng.uniform(*EPISODE_EVERY_DAYS))
            if cursor >= now:
                break
            events.extend(_episode(rng, facility_id, pump_id, flood_sensor_id, cursor, now))
        # Свежее мигание у части мокрых объектов: демо-стенд обязан показать
        # прогноз подтопления прямо сейчас, а не только в истории.
        if wet and rng.random() < 0.5:
            events.extend(_blink(rng, facility_id, pump_id, now - timedelta(hours=3)))
    return sensors, [row for row in events if row["occurred_at"] < now]
