"""Скачивает внешние ряды Москвы для пожарного риска: ветер и воздух.

Два источника Open-Meteo, оба открытые и бесплатные.

1. Архив погоды: скорость ветра и порывы, облачность, температура почвы на
   глубине 0–7 см. Ветер задувает внешний дым и пыль через вентшахты. Почва
   ближе всего к температуре грунта вокруг коллектора.
2. Архив качества воздуха CAMS: PM2.5, PM10, угарный газ. Смог от лесных и
   торфяных пожаров и дым салютов доходят до датчика дыма через шахты.

Салюты задаёт календарь, а не сеть: Новый год, 23 февраля, 9 мая, День
города в первую субботу сентября, 4 ноября и 12 июня.

Выход: `out/external_daily.parquet` по суткам Москвы. Сырые ответы лежат в
`out/external_*.json` и скачиваются заново, только если файлов нет.
"""

import datetime as dt
import json
import pathlib
import urllib.request

import duckdb

OUT = pathlib.Path(__file__).resolve().parent / "out"
DAILY = OUT / "external_daily.parquet"
SOURCES = {
    "weather": (
        "https://archive-api.open-meteo.com/v1/archive?latitude=55.75&longitude=37.62"
        "&start_date=2019-01-01&end_date=2026-06-30"
        "&hourly=wind_speed_10m,wind_gusts_10m,cloud_cover,soil_temperature_0_to_7cm"
        "&timezone=Europe%2FMoscow"
    ),
    "air": (
        "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=55.75&longitude=37.62"
        "&start_date=2019-01-01&end_date=2026-06-30"
        "&hourly=pm2_5,pm10,carbon_monoxide&timezone=Europe%2FMoscow"
    ),
}


def fetch(name: str, url: str) -> dict:
    raw = OUT / f"external_{name}.json"
    if not raw.exists():
        with urllib.request.urlopen(url, timeout=300) as response:
            raw.write_bytes(response.read())
    return json.loads(raw.read_text(encoding="utf-8"))["hourly"]


def fireworks(day: dt.date) -> bool:
    """Салют в Москве вечером этих суток или в ночь на следующие."""
    if (day.month, day.day) in ((12, 31), (1, 1), (2, 23), (5, 9), (6, 12), (11, 4)):
        return True
    # День города: первая суббота сентября.
    return day.month == 9 and day.weekday() == 5 and day.day <= 7


def main() -> None:
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect()
    for name, url in SOURCES.items():
        hourly = fetch(name, url)
        keys = [k for k in hourly if k != "time"]
        con.execute(
            f"CREATE TABLE {name} (t VARCHAR, {', '.join(f'{k} DOUBLE' for k in keys)})"
        )
        con.executemany(
            f"INSERT INTO {name} VALUES ({', '.join('?' * (len(keys) + 1))})",
            list(zip(hourly["time"], *(hourly[k] for k in keys), strict=True)),
        )
    days = []
    day = dt.date(2019, 1, 1)
    while day <= dt.date(2026, 6, 30):
        days.append((day, int(fireworks(day))))
        day += dt.timedelta(days=1)
    con.execute("CREATE TABLE fw (day DATE, fireworks INTEGER)")
    con.executemany("INSERT INTO fw VALUES (?, ?)", days)
    con.execute(
        f"""
        COPY (
            WITH w AS (
                SELECT strptime(t, '%Y-%m-%dT%H:%M')::DATE AS day,
                    avg(wind_speed_10m) AS wind_mean, max(wind_gusts_10m) AS gust_max,
                    avg(cloud_cover) AS cloud_mean, avg(soil_temperature_0_to_7cm) AS soil_temp
                FROM weather GROUP BY 1
            ),
            a AS (
                SELECT strptime(t, '%Y-%m-%dT%H:%M')::DATE AS day,
                    avg(pm2_5) AS pm25_mean, max(pm2_5) AS pm25_max,
                    avg(pm10) AS pm10_mean, max(carbon_monoxide) AS co_max
                FROM air GROUP BY 1
            )
            SELECT * FROM fw LEFT JOIN w USING (day) LEFT JOIN a USING (day) ORDER BY day
        ) TO '{DAILY.as_posix()}' (FORMAT PARQUET)
        """
    )
    print(con.execute(
        f"SELECT count(*), avg(wind_mean), avg(pm25_mean), count(pm25_mean), sum(fireworks) "
        f"FROM '{DAILY.as_posix()}'"
    ).fetchone())


if __name__ == "__main__":
    main()
