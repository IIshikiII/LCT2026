"""Погода недели потока по кругу. ADR 0018.

Загрузчик сдвигает архив погоды вместе с журналом, и погода кончается там же,
где история. Дальше поток проигрывает неделю выгрузки по кругу. Погода обязана
идти той же неделей: иначе к мартовским событиям подтопления пришёлся бы
сентябрь.

Загрузчик кладёт 168 часов погоды недели потока в `ingest_state` под ключом
`weather_slice`. Конвейер перед прогоном дописывает `weather_hourly` до
текущего часа: час берёт погоду того же часа недели.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.tables import ingest_state, weather_hourly

STATE_KEY = "weather_slice"
WEATHER_AREA = "MOSCOW"
MOSCOW = timedelta(hours=3)
HOUR = timedelta(hours=1)
COLUMNS = ("temperature_c", "humidity", "precip_mm", "rain_mm", "snowfall_cm", "snow_depth_m")


def slice_hours(
    hourly: dict[str, list[Any]], slice_start: datetime, days: int = 7
) -> list[dict[str, Any]]:
    """Часы недели потока из архива Open-Meteo в местном времени."""
    names = {
        "temperature_c": "temperature_2m",
        "humidity": "relative_humidity_2m",
        "precip_mm": "precipitation",
        "rain_mm": "rain",
        "snowfall_cm": "snowfall",
        "snow_depth_m": "snow_depth",
    }
    index = {stamp: i for i, stamp in enumerate(hourly["time"])}
    result = []
    for h in range(days * 24):
        stamp = (slice_start + h * HOUR).strftime("%Y-%m-%dT%H:%M")
        i = index.get(stamp)
        result.append(
            {
                column: (hourly.get(source) or [None] * len(hourly["time"]))[i]
                if i is not None
                else None
                for column, source in names.items()
            }
        )
    return result


def extend(conn: Connection, at: datetime) -> int:
    """Дописывает погоду до часа `at`. Отдаёт число новых часов."""
    state = conn.execute(
        select(ingest_state.c.value).where(ingest_state.c.key == STATE_KEY)
    ).scalar()
    if not state:
        return 0
    hours: list[dict[str, Any]] = state["hours"]
    origin = (
        datetime.fromisoformat(state["slice_start"]) + timedelta(days=int(state["shift_days"]))
    ) - MOSCOW
    origin = origin.replace(tzinfo=UTC)
    last = conn.execute(
        select(func.max(weather_hourly.c.observed_at)).where(
            weather_hourly.c.district == WEATHER_AREA
        )
    ).scalar()
    until = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    cursor = (last + HOUR) if last is not None else until - 24 * HOUR
    rows = []
    while cursor <= until:
        offset = int((cursor - origin) / HOUR) % len(hours)
        rows.append({"observed_at": cursor, "district": WEATHER_AREA, **hours[offset]})
        cursor += HOUR
    if not rows:
        return 0
    statement = pg_insert(weather_hourly).values(rows)
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=["observed_at", "district"],
            set_={name: statement.excluded[name] for name in COLUMNS},
        )
    )
    return len(rows)
