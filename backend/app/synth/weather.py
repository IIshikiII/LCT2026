"""Синтетическая погода Москвы для стенда без сети. ADR 0013.

Форма повторяет то, что модель подтопления читает из настоящего ряда: годовой
ход температуры, дожди сериями, снег при минусе и таяние покрова при плюсе.
Ряд пишется под районом `MOSCOW`, как настоящий из `app/ingest/weather.py`.

Случайность своя, от `seed + 2`. Потоки доступа и насосов от этого не
меняются.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any

from app.features.flood import WEATHER_AREA

# Средняя температура по дню года: минимум в конце января, максимум в июле.
MEAN_C, AMPLITUDE_C = 6.0, 13.0
COLDEST_DAY = 25
RAIN_START_PER_HOUR = 0.02
RAIN_HOURS = (2, 10)
RAIN_MM_PER_HOUR = (0.2, 2.5)
# Таяние: сантиметров покрова за час на каждый градус выше нуля.
MELT_CM_PER_DEGREE_HOUR = 0.05


def weather_rows(seed: int, now: datetime, history_days: int) -> list[dict[str, Any]]:
    """Отдаёт часы `weather_hourly` за `history_days` суток до `now`."""
    rng = random.Random(seed + 2)
    start = (now - timedelta(days=history_days)).replace(minute=0, second=0, microsecond=0)
    rows: list[dict[str, Any]] = []
    snow_cm = 0.0
    rain_left = 0
    intensity = 0.0
    moment = start
    while moment < now:
        day = moment.timetuple().tm_yday
        seasonal = MEAN_C - AMPLITUDE_C * math.cos(2 * math.pi * (day - COLDEST_DAY) / 365)
        daily = 4.0 * math.sin(2 * math.pi * (moment.hour - 9) / 24)
        temp = seasonal + daily + rng.gauss(0, 2)

        if rain_left == 0 and rng.random() < RAIN_START_PER_HOUR:
            rain_left = rng.randint(*RAIN_HOURS)
            intensity = rng.uniform(*RAIN_MM_PER_HOUR)
        precip = 0.0
        if rain_left > 0:
            precip = max(0.0, rng.gauss(intensity, intensity / 3))
            rain_left -= 1

        snowfall_cm = precip * 0.7 if temp < 0 else 0.0
        rain = 0.0 if temp < 0 else precip
        snow_cm += snowfall_cm
        if temp > 0:
            snow_cm = max(0.0, snow_cm - MELT_CM_PER_DEGREE_HOUR * temp)

        rows.append(
            {
                "observed_at": moment,
                "district": WEATHER_AREA,
                "temperature_c": round(temp, 1),
                "humidity": round(min(100.0, 70 + 25 * (precip > 0) + rng.gauss(0, 5)), 0),
                "precip_mm": round(precip, 2),
                "rain_mm": round(rain, 2),
                "snowfall_cm": round(snowfall_cm, 2),
                "snow_depth_m": round(snow_cm / 100, 3),
            }
        )
        moment += timedelta(hours=1)
    return rows
