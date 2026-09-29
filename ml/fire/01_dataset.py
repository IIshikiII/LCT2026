"""Отбирает тревоги направления «пожарный риск» и сводит их к единице
«объект, галерея, пикет». Порядок работы повторяет `ml/flood/01_dataset.py`.
Решения по метке лежат в ADR 0014.

1. Значений метки три, их задаёт пара «тип канала и значение». «Не замкнут»
   пишут одиннадцать типов, пожаром он является только у теплового датчика.
2. Круг единиц задан типами каналов, а не тревогами. Единица входит в выборку,
   если на её пикете стоит датчик дыма, датчик температуры или тепловой
   датчик. Единица без единой тревоги тоже наблюдается.
3. Ручной извещатель в метку не входит: 97 % его тревог лежат в окне
   «будни 8–16», это проверки.

Скрипт пишет три файла.

- `fire_alarms.parquet`: каждая тревога поштучно, с каналом и секундой. Форму
  события по ней считает классификатор проверок `04_pu_label.py`.
- `fire_hourly.parquet`: часы с тревогой по значению.
- `fire_units.parquet`: границы жизни каждой единицы.
"""

import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "eda" / "out" / "channels.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "out"
OUT_ALARMS = OUT / "fire_alarms.parquet"
OUT_HOURLY = OUT / "fire_hourly.parquet"
OUT_UNITS = OUT / "fire_units.parquet"

UNIT_TYPES = ("Датчик дыма", "Датчик температуры", "Тепловой датчик")
LABEL_PAIRS = (
    ("Датчик дыма", "Обнаружен дым"),
    ("Датчик температуры", "Температура выше 40ºC"),
    ("Тепловой датчик", "Не замкнут"),
)
ARTIFACT_OID = 5343
ARTIFACT_YEARS = (2020, 2021)

OUT.mkdir(exist_ok=True)
con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")

pairs = ", ".join(f"('{t}', '{v}')" for t, v in LABEL_PAIRS)

t = time.time()
con.execute(
    f"""
    CREATE TABLE fire_all AS
    SELECT
        ev.channel_id AS channel_id,
        ch.stype AS stype,
        ev.value AS value,
        ch.oid AS object_id,
        ch.gal_key AS gallery,
        ch.picket AS picket,
        ev.d AS day,
        ev.t AS sec
    FROM read_parquet('{EVENTS.as_posix()}') ev
    JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
    WHERE ev.alarm
      AND (ch.stype, ev.value) IN ({pairs})
      AND NOT (ch.oid = {ARTIFACT_OID} AND year(ev.d) IN {ARTIFACT_YEARS})
    """
)
con.execute("CREATE VIEW fire AS SELECT * FROM fire_all WHERE picket IS NOT NULL")
print(f"тревоги отобраны за {time.time() - t:.0f} c")

for value, total, lost in con.execute(
    """
    SELECT value, count(*), count(*) FILTER (WHERE picket IS NULL)
    FROM fire_all GROUP BY 1 ORDER BY 1
    """
).fetchall():
    print(
        f"«{value}»: тревог {total}, без пикета {lost} ({100.0 * lost / total:.1f} %)"
    )

con.execute(
    f"""
    COPY (
        SELECT DISTINCT channel_id, stype, value, object_id, gallery, picket,
            day + sec AS moment
        FROM fire
        ORDER BY object_id, moment, channel_id
    ) TO '{OUT_ALARMS.as_posix()}' (FORMAT PARQUET)
    """
)

t = time.time()
con.execute(
    f"""
    COPY (
        SELECT
            object_id,
            gallery,
            picket,
            value,
            date_trunc('hour', day + sec) AS hour,
            count(*) AS n_alarms,
            count(DISTINCT (channel_id, day, sec)) AS n_moments,
            count(DISTINCT channel_id) AS n_channels
        FROM fire
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY 1, 2, 3, 4, 5
    ) TO '{OUT_HOURLY.as_posix()}' (FORMAT PARQUET)
    """
)
print(f"часовой свод построен за {time.time() - t:.0f} c")

# Жизнь единицы считается по всем записям её датчиков, а не по одним тревогам.
t = time.time()
con.execute(
    f"""
    COPY (
        WITH chan AS (
            SELECT cid AS channel_id, stype, oid AS object_id,
                gal_key AS gallery, picket
            FROM read_parquet('{CHANNELS.as_posix()}')
            WHERE stype IN {UNIT_TYPES} AND picket IS NOT NULL
        ),
        life AS (
            SELECT channel_id, min(d) AS d_from, max(d) AS d_to
            FROM read_parquet('{EVENTS.as_posix()}')
            WHERE channel_id IN (SELECT channel_id FROM chan)
            GROUP BY 1
        )
        SELECT
            c.object_id,
            c.gallery,
            c.picket,
            date_trunc('hour', min(life.d_from)::TIMESTAMP) AS hour_from,
            date_trunc('hour', max(life.d_to)::TIMESTAMP + INTERVAL 23 HOUR) AS hour_to,
            count(DISTINCT c.channel_id) AS n_channels,
            count(DISTINCT c.channel_id) FILTER (WHERE c.stype = 'Датчик дыма') AS n_smoke,
            count(DISTINCT c.channel_id) FILTER (WHERE c.stype = 'Датчик температуры') AS n_temp,
            count(DISTINCT c.channel_id) FILTER (WHERE c.stype = 'Тепловой датчик') AS n_heat
        FROM chan c
        JOIN life ON life.channel_id = c.channel_id
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    ) TO '{OUT_UNITS.as_posix()}' (FORMAT PARQUET)
    """
)
print(f"жизнь единиц посчитана за {time.time() - t:.0f} c")

stats = con.execute(
    f"""
    SELECT
        (SELECT count(*) FROM read_parquet('{OUT_ALARMS.as_posix()}')),
        (SELECT count(*) FROM read_parquet('{OUT_HOURLY.as_posix()}')),
        (SELECT count(*) FROM read_parquet('{OUT_UNITS.as_posix()}')),
        (SELECT sum(n_channels) FROM read_parquet('{OUT_UNITS.as_posix()}')),
        (SELECT min(hour) FROM read_parquet('{OUT_HOURLY.as_posix()}')),
        (SELECT max(hour) FROM read_parquet('{OUT_HOURLY.as_posix()}'))
    """
).fetchone()
print(f"моментов тревоги: {stats[0]}")
print(f"строк «единица, значение, час»: {stats[1]}")
print(f"единиц «объект, галерея, пикет»: {stats[2]}")
print(f"каналов дыма, температуры и тепловых: {stats[3]}")
print(f"период: {stats[4]} — {stats[5]}")
