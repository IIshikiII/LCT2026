"""Разбор ответа Open-Meteo в строки `weather_hourly`. ADR 0013."""

from __future__ import annotations

from datetime import UTC, datetime

from app.features.flood import WEATHER_AREA
from app.ingest.weather import rows

PAYLOAD = {
    "hourly": {
        "time": ["2026-09-25T10:00", "2026-09-25T11:00", "2026-09-25T12:00"],
        "temperature_2m": [14.0, 15.0, 16.0],
        "relative_humidity_2m": [80, 75, 70],
        "precipitation": [0.4, 0.0, 1.2],
        "rain": [0.4, 0.0, 1.2],
        "snowfall": [0.0, 0.0, 0.0],
        "snow_depth": [0.0, 0.0, 0.0],
    }
}


def test_the_hours_land_in_utc_under_the_moscow_area() -> None:
    items = rows(PAYLOAD, datetime(2026, 9, 25, 12, 0, tzinfo=UTC))
    assert [item["observed_at"].hour for item in items] == [10, 11]
    assert all(item["observed_at"].tzinfo is UTC for item in items)
    assert {item["district"] for item in items} == {WEATHER_AREA}
    assert items[0]["precip_mm"] == 0.4
    assert items[0]["rain_mm"] == 0.4
    assert items[1]["temperature_c"] == 15.0


def test_the_forecast_hours_are_not_stored() -> None:
    """Прогноз погоды признаком не является: модель училась на факте."""
    items = rows(PAYLOAD, datetime(2026, 9, 25, 10, 30, tzinfo=UTC))
    assert [item["observed_at"].hour for item in items] == [10]
