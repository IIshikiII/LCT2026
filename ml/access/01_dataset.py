"""Отбирает тревоги доступа из журнала и сводит их к единице «объект, галерея,
пикет» по часам. Единица без пикета (канал вне трассы) в свод не идёт: у неё
нет координаты. Источник координаты канала — `notebooks/out/channel_features.parquet`
из тетради 01."""
import time
import pathlib
import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "out" / "access_hourly.parquet"

ACCESS_VALUES = ("Не замкнут", "Обнаружено движение", "Рычаг сдернут")

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")

t = time.time()
con.execute(
    f"""
    COPY (
        SELECT
            ch.oid AS object_id,
            ch.gal_key AS gallery,
            ch.picket AS picket,
            date_trunc('hour', ev.d + ev.t) AS hour,
            count(*) AS n_alarms,
            count(DISTINCT ev.channel_id) AS n_channels
        FROM read_parquet('{EVENTS.as_posix()}') ev
        JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
        WHERE ev.alarm
          AND ev.value IN {ACCESS_VALUES}
          AND ch.picket IS NOT NULL
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    ) TO '{OUT.as_posix()}' (FORMAT PARQUET)
    """
)
print(f"свод построен за {time.time() - t:.0f} c")

stats = con.execute(
    f"""
    SELECT
        count(*) AS n_rows,
        count(DISTINCT (object_id, gallery, picket)) AS n_units,
        min(hour) AS hmin,
        max(hour) AS hmax
    FROM read_parquet('{OUT.as_posix()}')
    """
).fetchone()
print(f"строк: {stats[0]}")
print(f"единиц «объект, галерея, пикет»: {stats[1]}")
print(f"период: {stats[2]} — {stats[3]}")
