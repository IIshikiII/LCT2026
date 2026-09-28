"""Живые графики карточек доступа и подтопления. ADR 0018.

Правило одно на все графики карточки: ряд без единого ненулевого значения в
блок не идёт. Если не осталось ни одного ряда, блок берёт запасной ряд,
который есть всегда: события доступа по сети или температуру воздуха.
Диспетчер не должен видеть пустую ось.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features.access import ACCESS_ALARM_TYPES, SECURITY_ARMED, SECURITY_DISARMED
from app.features.flood import LABEL_TYPES, PUMP_OFF, PUMP_ON
from app.ml.protocol import Block

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
MOSCOW = timedelta(hours=3)


def _series(name: str, unit: str, points: list[tuple[datetime, float]]) -> dict[str, Any] | None:
    if not points or not any(v != 0 for _, v in points):
        return None
    return {
        "name": name,
        "unit": unit,
        "points": [{"t": t.isoformat(), "v": round(float(v), 2)} for t, v in points],
    }


def _daily_counts(
    conn: Connection, facility: str | None, types: tuple[str, ...], at: datetime, days: int
) -> list[tuple[datetime, float]]:
    """События по московским суткам. Пустой `facility` значит вся сеть."""
    start = (at.astimezone(UTC) + MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start - MOSCOW - (days - 1) * DAY
    scope = "AND facility_id = :facility" if facility else ""
    found = (
        conn.execute(
            text(
                f"""
                SELECT floor(extract(epoch FROM (occurred_at - :start)) / 86400)::int, count(*)
                FROM alarm_event
                WHERE alarm_type = ANY(:types) AND occurred_at >= :start AND occurred_at < :at
                {scope}
                GROUP BY 1
                """
            ),
            {"types": list(types), "start": start, "at": at, "facility": facility},
        ).all()
    )
    rows: dict[int, int] = {int(day): int(n) for day, n in found}
    return [(start + i * DAY + 12 * HOUR, float(rows.get(i, 0))) for i in range(days)]


def _weather(
    conn: Connection, column: str, at: datetime, hours: int
) -> list[tuple[datetime, float]]:
    rows = conn.execute(
        text(
            f"""
            SELECT observed_at, {column} FROM weather_hourly
            WHERE district = 'MOSCOW' AND {column} IS NOT NULL
              AND observed_at >= :start AND observed_at < :at
            ORDER BY 1
            """
        ),
        {"start": at - hours * HOUR, "at": at},
    ).all()
    return [(t, float(v)) for t, v in rows]


def _state_hours(
    conn: Connection, facility: str, on: str, off: str, at: datetime, hours: int
) -> list[tuple[datetime, float]]:
    """Доля каждого часа в состоянии `on` по событиям смены состояния."""
    start = (at - hours * HOUR).replace(minute=0, second=0, microsecond=0)
    rows = conn.execute(
        text(
            """
            (SELECT occurred_at, alarm_type FROM alarm_event
             WHERE facility_id = :f AND alarm_type IN (:on, :off) AND occurred_at < :start
             ORDER BY occurred_at DESC LIMIT 1)
            UNION ALL
            (SELECT occurred_at, alarm_type FROM alarm_event
             WHERE facility_id = :f AND alarm_type IN (:on, :off)
               AND occurred_at >= :start AND occurred_at < :at
             ORDER BY occurred_at)
            """
        ),
        {"f": facility, "on": on, "off": off, "start": start, "at": at},
    ).all()
    if not rows:
        return []
    changes = sorted((t, kind == on) for t, kind in rows)
    points = []
    for h in range(hours):
        lo, hi = start + h * HOUR, start + (h + 1) * HOUR
        state = False
        for t, value in changes:
            if t <= lo:
                state = value
        share, cursor = 0.0, lo
        for t, value in changes:
            if lo < t < hi:
                share += (t - cursor).total_seconds() if state else 0.0
                cursor, state = t, value
        share += (hi - cursor).total_seconds() if state else 0.0
        points.append((lo + HOUR / 2, share / 3600.0))
    return points


def _block(title: str, series: list[dict[str, Any] | None], at: datetime) -> Block:
    kept = [s for s in series if s is not None]
    return Block(type="timeseries", title=title, data={"series": kept, "markerAt": at.isoformat()})


def access_blocks(conn: Connection, facility_id: str, at: datetime) -> list[Block]:
    """Тревоги участка по суткам и режим охраны по часам."""
    series = [
        _series(
            "Тревоги доступа на участке по суткам",
            "шт.",
            _daily_counts(conn, facility_id, ACCESS_ALARM_TYPES, at, 30),
        ),
        _series(
            "Объект на охране, доля часа",
            "доля",
            _state_hours(conn, facility_id, SECURITY_ARMED, SECURITY_DISARMED, at, 72),
        ),
    ]
    if not any(series):
        series.append(
            _series(
                "Тревоги доступа по всей сети по суткам",
                "шт.",
                _daily_counts(conn, None, ACCESS_ALARM_TYPES, at, 30),
            )
        )
    block = _block("Участок и сеть", series, at)
    return [block] if block.data["series"] else []


def flood_blocks(conn: Connection, facility_id: str, at: datetime) -> list[Block]:
    """Работа насоса, сигналы затопления и осадки."""
    series = [
        _series(
            "Насос включён, доля часа",
            "доля",
            _state_hours(conn, facility_id, PUMP_ON, PUMP_OFF, at, 72),
        ),
        _series(
            "Сигналы затопления по суткам",
            "шт.",
            _daily_counts(conn, facility_id, LABEL_TYPES, at, 30),
        ),
        _series("Осадки в Москве", "мм", _weather(conn, "precip_mm", at, 72)),
    ]
    if not any(series):
        series.append(
            _series("Температура воздуха в Москве", "°C", _weather(conn, "temperature_c", at, 72))
        )
    block = _block("Пикет и погода", series, at)
    return [block] if block.data["series"] else []
