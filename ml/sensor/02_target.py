"""Меряет метку направления «отказ датчика» на витрине из `01_dataset.py` и
пишет замер в `out/target_stats.json`.

Расчёт берётся из `ml/access/target_stats.py` без единой правки. Модуль лежит у
направления доступа, а не в общей папке, потому что он принадлежит первому
направлению и переезд сломал бы три скрипта. Переносится порядок работы, а не
расположение файлов.

Метка та же по форме, что у доступа: единица имеет тревогу направления в окне
(t, t + 24 ч]. Разбор чисел лежит в записи журнала по задаче T36."""
import json
import pathlib
import sys

import duckdb

ACCESS = pathlib.Path(__file__).resolve().parents[1] / "access"
sys.path.insert(0, str(ACCESS))

import target_stats  # noqa: E402  модуль соседнего направления, путь задан выше

OUT = pathlib.Path(__file__).resolve().parent / "out"
HOURLY = OUT / "sensor_hourly.parquet"
UNITS = OUT / "sensor_units.parquet"
STATS = OUT / "target_stats.json"

HORIZON_HOURS = 24

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")
target_stats.attach(con, HOURLY, "alarm")
target_stats.attach(con, UNITS, "unit")

result = {
    "direction": "SENSOR_FAILURE",
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
