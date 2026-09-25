"""Погода Москвы из Open-Meteo в `weather_hourly`. ADR 0013.

Модель подтопления училась на архиве Open-Meteo для точки 55,75 с. ш.,
37,62 в. д. (`ml/flood/08_weather.py`). Сервис берёт те же величины из того же
источника: прогнозный API отдаёт прошедшие часы за `past_days` суток. Строки
ложатся под район `MOSCOW`, по нему их читают признаки.

Ряд пишется только до текущего часа: прогноз погоды признаком не является,
модель училась на фактической погоде.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.features.flood import WEATHER_AREA
from app.tables import weather_hourly

URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.62"
    "&hourly=precipitation,rain,snowfall,temperature_2m,relative_humidity_2m,snow_depth"
    "&timezone=GMT&forecast_days=1&past_days={days}"
)
MAX_PAST_DAYS = 92


def fetch(days: int) -> dict[str, Any]:
    """Читает ответ Open-Meteo за последние `days` суток."""
    days = max(1, min(days, MAX_PAST_DAYS))
    with urllib.request.urlopen(URL.format(days=days), timeout=60) as response:
        payload: dict[str, Any] = json.loads(response.read())
    return payload


def rows(payload: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Строки `weather_hourly` из ответа. Часы с `now` и позже отбрасываются."""
    hourly = payload["hourly"]
    result = []
    for index, stamp in enumerate(hourly["time"]):
        observed = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        if observed >= now:
            continue
        result.append(
            {
                "observed_at": observed,
                "district": WEATHER_AREA,
                "temperature_c": hourly["temperature_2m"][index],
                "humidity": hourly["relative_humidity_2m"][index],
                "precip_mm": hourly["precipitation"][index],
                "rain_mm": hourly["rain"][index],
                "snowfall_cm": hourly["snowfall"][index],
                "snow_depth_m": hourly["snow_depth"][index],
            }
        )
    return result


def upsert(conn: Connection, items: list[dict[str, Any]]) -> int:
    """Пишет строки. Повтор часа заменяет прежние значения: архив уточняется."""
    if not items:
        return 0
    statement = pg_insert(weather_hourly).values(items)
    columns = ("temperature_c", "humidity", "precip_mm", "rain_mm", "snowfall_cm", "snow_depth_m")
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=["observed_at", "district"],
            set_={name: statement.excluded[name] for name in columns},
        )
    )
    return len(items)


def load(conn: Connection, days: int, now: datetime | None = None) -> int:
    """Загружает погоду за последние `days` суток. Отдаёт число часов."""
    moment = now or datetime.now(UTC)
    return upsert(conn, rows(fetch(days), moment))
