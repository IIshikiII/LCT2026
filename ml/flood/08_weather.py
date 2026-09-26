"""Погода Москвы из открытого архива Open-Meteo для направления подтопления.

ТЗ §13 называет открытые метеоданные штатным внешним источником, а сценарий
подтопления в ТЗ §12 прямо опирается на прогноз погоды. Здесь нужен архив: он
проверяет критерий настоящего подтопления (ADR 0010) и даёт признаки модели.

Точка одна, центр Москвы. Сеть коллекторов в выгрузке это один район, и
погода внутри города для суточного прогноза почти одинакова.

Время архива московское, как и в журнале СМВУ.

Выход: `out/weather_hourly.parquet`, `out/weather_daily.parquet`. Сырой ответ
архива лежит в `out/weather_moscow_hourly.json` и скачивается заново, только
когда файла нет.
"""

import json
import pathlib
import urllib.request

import duckdb

OUT = pathlib.Path(__file__).resolve().parent / "out"
RAW = OUT / "weather_moscow_hourly.json"
HOURLY = OUT / "weather_hourly.parquet"
DAILY = OUT / "weather_daily.parquet"

URL = (
    "https://archive-api.open-meteo.com/v1/archive?latitude=55.75&longitude=37.62"
    "&start_date=2019-01-01&end_date=2026-06-30"
    "&hourly=precipitation,rain,snowfall,temperature_2m,snow_depth"
    "&timezone=Europe%2FMoscow"
)

OUT.mkdir(exist_ok=True)
if not RAW.exists():
    with urllib.request.urlopen(URL, timeout=120) as response:
        RAW.write_bytes(response.read())

payload = json.loads(RAW.read_text(encoding="utf-8"))["hourly"]
rows = list(
    zip(
        payload["time"],
        payload["precipitation"],
        payload["rain"],
        payload["snowfall"],
        payload["temperature_2m"],
        payload["snow_depth"],
        strict=True,
    )
)

con = duckdb.connect()
con.execute(
    "CREATE TABLE w (t VARCHAR, precip DOUBLE, rain DOUBLE, snowfall DOUBLE, "
    "temp DOUBLE, snow_depth DOUBLE)"
)
con.executemany("INSERT INTO w VALUES (?, ?, ?, ?, ?, ?)", rows)
con.execute(
    f"""
    COPY (
        SELECT strptime(t, '%Y-%m-%dT%H:%M') AS hour,
               coalesce(precip, 0) AS precip, coalesce(rain, 0) AS rain,
               coalesce(snowfall, 0) AS snowfall, temp, snow_depth
        FROM w ORDER BY hour
    ) TO '{HOURLY.as_posix()}' (FORMAT PARQUET)
    """
)
# Таяние: насколько уменьшился снежный покров за сутки, в сантиметрах.
con.execute(
    f"""
    COPY (
        WITH d AS (
            SELECT hour::DATE AS day,
                   sum(precip) AS precip_mm, sum(rain) AS rain_mm,
                   sum(snowfall) AS snowfall_cm,
                   avg(temp) AS temp_mean, max(temp) AS temp_max, min(temp) AS temp_min,
                   arg_max(snow_depth, hour) AS snow_depth_end_m
            FROM read_parquet('{HOURLY.as_posix()}') GROUP BY 1
        )
        SELECT *,
               greatest(0, 100 * (lag(snow_depth_end_m) OVER (ORDER BY day)
                                  - snow_depth_end_m)) AS melt_cm
        FROM d ORDER BY day
    ) TO '{DAILY.as_posix()}' (FORMAT PARQUET)
    """
)
print(
    con.execute(
        f"""
        SELECT count(*), min(day), max(day), round(sum(precip_mm) / 7.5),
               count(*) FILTER (WHERE melt_cm >= 3)
        FROM read_parquet('{DAILY.as_posix()}')
        """
    ).fetchone()
)
