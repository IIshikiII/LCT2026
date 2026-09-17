"""Меряет метку направления «несанкционированный доступ» на витрине из
`01_dataset.py` и пишет замер в `out/target_stats.json`.

Сам расчёт лежит в `target_stats.py`. Этот скрипт только подключает витрину и
печатает итог. Вторую версию метки, без окон «Снято с охраны», меряет
`03_disarm.py`.
"""
import json
import pathlib

import duckdb

import target_stats

OUT = pathlib.Path(__file__).resolve().parent / "out"
HOURLY = OUT / "access_hourly.parquet"
UNITS = OUT / "access_units.parquet"
STATS = OUT / "target_stats.json"

HORIZON_HOURS = 24

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")
target_stats.attach(con, HOURLY, "alarm")
target_stats.attach(con, UNITS, "unit")

result = {
    "horizon_hours": HORIZON_HOURS,
    "units": con.execute("SELECT count(*) FROM unit").fetchone()[0],
    "channels": con.execute("SELECT sum(n_channels) FROM unit").fetchone()[0],
    "alarm_hours": con.execute("SELECT count(*) FROM alarm").fetchone()[0],
    "alarm_moments": int(con.execute("SELECT sum(n_moments) FROM alarm").fetchone()[0]),
    "hourly": target_stats.hourly_stats(con, HORIZON_HOURS),
    "daily": target_stats.daily_stats(con),
}

STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"единиц: {result['units']}, каналов: {result['channels']}")
print(f"часов с тревогой: {result['alarm_hours']}, моментов: {result['alarm_moments']}")
print(target_stats.line(result["hourly"]))
print(target_stats.line(result["daily"]))
print(f"замер записан в {STATS}")
