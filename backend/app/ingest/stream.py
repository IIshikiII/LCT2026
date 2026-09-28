"""Приём потока СМВУ. ADR 0017.

Строка потока повторяет строку журнала `журнал_событий_пример.csv`: номер
события, номер канала, дата, время, флаг «тревожное», значение датчика. Дата
и время местные, московские.

Канал находит датчик по `sensor.channel_id`. Датчик режима охраны стоит на
каждом участке объекта, поэтому одна строка может дать несколько событий.
Значение переводит в код события то же правило, что у загрузчика
(`app/ingest/mapping.py`). Число температуры и метана ложится в час: в базе
остаётся наибольшее значение канала за час, как в истории.

Строка неизвестного канала или без события отбрасывается и считается в
ответе. Поток не падает на чужой строке: СМВУ шлёт всё, а сервису нужна малая
часть.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.ingest import mapping

MOSCOW = timezone(timedelta(hours=3))
TRUE_VALUES = frozenset({"t", "true", "1", "да"})


@dataclass(frozen=True)
class StreamRow:
    """Строка потока после разбора."""

    event_id: int | None
    channel_id: int
    at: datetime
    alarm: bool
    value: str | None


@dataclass(frozen=True)
class Accepted:
    """Итог приёма пачки."""

    received: int
    events: int
    readings: int
    skipped: int


def parse_row(
    event_id: int | None, channel_id: int, day: str, clock: str, alarm: Any, value: str | None
) -> StreamRow:
    """Строка потока из полей журнала. Время местное московское."""
    at = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=MOSCOW)
    flag = alarm if isinstance(alarm, bool) else str(alarm).strip().lower() in TRUE_VALUES
    return StreamRow(event_id, int(channel_id), at, flag, value)


def accept(conn: Connection, rows: list[StreamRow], received_at: datetime | None) -> Accepted:
    """Пишет события и показания пачки. Отдаёт счёт принятого.

    Пустой `received_at` значит строки истории, а не потока: загрузчик так
    засевает сценарии, и задержка потока их не считает.
    """
    if not rows:
        return Accepted(0, 0, 0, 0)
    sensors: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    for sid, fid, channel_type, channel_id in conn.execute(
        text(
            "SELECT id, facility_id, channel_type, channel_id FROM sensor "
            "WHERE channel_id = ANY(:ids) AND is_active"
        ),
        {"ids": sorted({r.channel_id for r in rows})},
    ).all():
        sensors[int(channel_id)].append((str(sid), str(fid), str(channel_type)))

    events: list[dict[str, Any]] = []
    readings: dict[tuple[str, str, datetime], dict[str, Any]] = {}
    skipped = 0
    for row in rows:
        targets = sensors.get(row.channel_id)
        if not targets:
            skipped += 1
            continue
        used = False
        for sensor_id, facility_id, channel_type in targets:
            code = mapping.event_code(channel_type, row.value, row.alarm)
            if code is not None:
                events.append(
                    {
                        "sensor_id": sensor_id,
                        "facility_id": facility_id,
                        "occurred_at": row.at,
                        "alarm_type": code,
                        "source_event_id": row.event_id,
                        "received_at": received_at,
                    }
                )
                used = True
                continue
            number = mapping.reading(channel_type, row.value)
            if number is not None:
                spec, value = number
                hour = row.at.replace(minute=0, second=0, microsecond=0)
                key = (sensor_id, spec.metric, hour)
                current = readings.get(key)
                if current is None or value > current["value"]:
                    readings[key] = {
                        "sensor_id": sensor_id,
                        "facility_id": facility_id,
                        "metric": spec.metric,
                        "observed_at": hour,
                        "value": value,
                        "unit": spec.unit,
                    }
                used = True
        if not used:
            skipped += 1

    if events:
        conn.execute(
            text(
                """
                INSERT INTO alarm_event
                    (sensor_id, facility_id, occurred_at, alarm_type, source_event_id, received_at)
                VALUES
                    (:sensor_id, :facility_id, :occurred_at, :alarm_type, :source_event_id,
                     :received_at)
                """
            ),
            events,
        )
    if readings:
        conn.execute(
            text(
                """
                INSERT INTO sensor_reading
                    (sensor_id, facility_id, metric, observed_at, value, unit)
                VALUES (:sensor_id, :facility_id, :metric, :observed_at, :value, :unit)
                ON CONFLICT (sensor_id, metric, observed_at)
                DO UPDATE SET value = greatest(sensor_reading.value, excluded.value)
                """
            ),
            list(readings.values()),
        )
    return Accepted(len(rows), len(events), len(readings), skipped)
