"""Скачивает влажность, точку росы и давление Москвы из архива Open-Meteo.

Температура и осадки уже лежат у подтопления (`ml/flood/08_weather.py`).
Пожару нужна влага: конденсат и туман дают датчику дыма ложный дым. ТЗ §13
называет влажность и давление среди штатных метеоданных.

Выход: `out/humidity_daily.parquet` по суткам Москвы: средняя и наибольшая
влажность, средняя точка росы, среднее давление и его изменение за сутки.
Сырой ответ лежит в `out/humidity_moscow_hourly.json` и скачивается заново,
только если файла нет.
"""

import json
import pathlib
import urllib.request

import duckdb

OUT = pathlib.Path(__file__).resolve().parent / "out"
RAW = OUT / "humidity_moscow_hourly.json"
DAILY = OUT / "humidity_daily.parquet"
URL = (
    "https://archive-api.open-meteo.com/v1/archive?latitude=55.75&longitude=37.62"
    "&start_date=2019-01-01&end_date=2026-06-30"
    "&hourly=relative_humidity_2m,dew_point_2m,surface_pressure"
    "&timezone=Europe%2FMoscow"
)

OUT.mkdir(exist_ok=True)
if not RAW.exists():
    with urllib.request.urlopen(URL, timeout=180) as response:
        RAW.write_bytes(response.read())

payload = json.loads(RAW.read_text(encoding="utf-8"))["hourly"]
rows = list(
    zip(
        payload["time"],
        payload["relative_humidity_2m"],
        payload["dew_point_2m"],
        payload["surface_pressure"],
        strict=True,
    )
)
con = duckdb.connect()
con.execute("CREATE TABLE w (t VARCHAR, rh DOUBLE, dew DOUBLE, pressure DOUBLE)")
con.executemany("INSERT INTO w VALUES (?, ?, ?, ?)", rows)
con.execute(
    f"""
    COPY (
        WITH d AS (
            SELECT strptime(t, '%Y-%m-%dT%H:%M')::DATE AS day,
                avg(rh) AS rh_mean, max(rh) AS rh_max, avg(dew) AS dew_mean,
                avg(pressure) AS pressure_mean
            FROM w GROUP BY 1
        )
        SELECT *, pressure_mean - lag(pressure_mean) OVER (ORDER BY day) AS pressure_change
        FROM d ORDER BY day
    ) TO '{DAILY.as_posix()}' (FORMAT PARQUET)
    """
)
print(con.execute(f"SELECT count(*), min(day), max(day), avg(rh_mean) FROM '{DAILY.as_posix()}'").fetchone())
