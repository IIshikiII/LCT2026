"""Отбирает тревоги доступа из журнала и сводит их к единице «объект, галерея,
пикет» по часам. Единица без пикета (канал вне трассы) в свод не идёт: у неё
нет координаты. Источник координаты канала — `notebooks/out/channel_features.parquet`
из тетради 01.

Скрипт пишет два файла. `access_hourly.parquet` держит часы с тревогой,
`access_units.parquet` держит границы жизни каждой единицы. Второй файл нужен
потому, что часы без тревоги тоже входят в выборку, и их число считается от
жизни единицы.

Определение метки лежит в `backend/docs/adr/0001-access-target.md`."""
import pathlib
import time

import commissioning
import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "out"
OUT_HOURLY = OUT / "access_hourly.parquet"
OUT_UNITS = OUT / "access_units.parquet"

ACCESS_VALUES = ("Не замкнут", "Обнаружено движение", "Рычаг сдернут")
ARTIFACT_OID = 5343
ARTIFACT_YEARS = (2020, 2021)

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")

# Период ввода объекта в работу. Разбор и числа лежат в commissioning.py.
con.execute(commissioning.table_sql(EVENTS.as_posix(), CHANNELS.as_posix()))

t = time.time()
con.execute(
    f"""
    CREATE TABLE access_all AS
    SELECT
        ev.channel_id AS channel_id,
        ch.oid AS object_id,
        ch.gal_key AS gallery,
        ch.picket AS picket,
        ev.d AS day,
        ev.t AS sec
    FROM read_parquet('{EVENTS.as_posix()}') ev
    JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
    JOIN commissioning cm ON cm.object_id = ch.oid
    WHERE ev.alarm
      AND ev.value IN {ACCESS_VALUES}
      AND ev.d >= cm.ready_day
      AND NOT (ch.oid = {ARTIFACT_OID} AND year(ev.d) IN {ARTIFACT_YEARS})
    """
)
con.execute("CREATE VIEW access AS SELECT * FROM access_all WHERE picket IS NOT NULL")
print(f"тревоги отобраны за {time.time() - t:.0f} c")

t = time.time()
con.execute(
    f"""
    COPY (
        SELECT
            object_id,
            gallery,
            picket,
            date_trunc('hour', day + sec) AS hour,
            count(*) AS n_alarms,
            count(DISTINCT (channel_id, day, sec)) AS n_moments,
            count(DISTINCT channel_id) AS n_channels
        FROM access
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    ) TO '{OUT_HOURLY.as_posix()}' (FORMAT PARQUET)
    """
)
print(f"часовой свод построен за {time.time() - t:.0f} c")

# Жизнь единицы считается по всем записям её каналов доступа, а не по одним
# тревогам: канал молчит годами и всё это время наблюдается.
t = time.time()
con.execute(
    f"""
    COPY (
        WITH life AS (
            SELECT ev.channel_id,
                   greatest(min(ev.d), max(cm.ready_day)) AS d_from,
                   max(ev.d) AS d_to
            FROM read_parquet('{EVENTS.as_posix()}') ev
            JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
            JOIN commissioning cm ON cm.object_id = ch.oid
            WHERE ev.channel_id IN (SELECT DISTINCT channel_id FROM access)
            GROUP BY 1
        )
        SELECT
            a.object_id,
            a.gallery,
            a.picket,
            date_trunc('hour', min(life.d_from)::TIMESTAMP) AS hour_from,
            date_trunc('hour', max(life.d_to)::TIMESTAMP + INTERVAL 23 HOUR) AS hour_to,
            count(DISTINCT a.channel_id) AS n_channels
        FROM (SELECT DISTINCT channel_id, object_id, gallery, picket FROM access) a
        JOIN life ON life.channel_id = a.channel_id
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    ) TO '{OUT_UNITS.as_posix()}' (FORMAT PARQUET)
    """
)
print(f"жизнь единиц посчитана за {time.time() - t:.0f} c")

# Сверка с `hackathon-gap-analysis.md` §A4. Там замер сделан на канало-днях, а
# не на единицах, поэтому числа обязаны разойтись. Блок показывает, что
# расходится именно сетка, а не отбор строк.
check = con.execute(
    f"""
    WITH life AS (
        SELECT channel_id, min(d) AS d_from, max(d) AS d_to
        FROM read_parquet('{EVENTS.as_posix()}')
        WHERE channel_id IN (SELECT DISTINCT channel_id FROM access_all)
        GROUP BY 1
    ),
    event AS (SELECT DISTINCT channel_id, day FROM access_all)
    SELECT
        (SELECT count(*) FROM life),
        (SELECT sum(date_diff('day', d_from, d_to) + 1) FROM life),
        (SELECT count(*) FROM event),
        (SELECT count(*) FROM event e
         WHERE exists(SELECT 1 FROM event n
                      WHERE n.channel_id = e.channel_id AND n.day = e.day + 1))
    """
).fetchone()
print(
    f"сверка §A4: каналов {check[0]}, канало-дней {check[1]}, "
    f"событий {check[2]}, база {100.0 * check[2] / check[1]:.3f} %, "
    f"наивная точность {100.0 * check[3] / check[2]:.3f} %"
)

stats = con.execute(
    f"""
    SELECT
        (SELECT count(*) FROM read_parquet('{OUT_HOURLY.as_posix()}')),
        (SELECT sum(n_moments) FROM read_parquet('{OUT_HOURLY.as_posix()}')),
        (SELECT count(*) FROM read_parquet('{OUT_UNITS.as_posix()}')),
        (SELECT sum(n_channels) FROM read_parquet('{OUT_UNITS.as_posix()}')),
        (SELECT min(hour) FROM read_parquet('{OUT_HOURLY.as_posix()}')),
        (SELECT max(hour) FROM read_parquet('{OUT_HOURLY.as_posix()}'))
    """
).fetchone()
print(f"часов с тревогой: {stats[0]}")
print(f"моментов тревоги: {stats[1]}")
print(f"единиц «объект, галерея, пикет»: {stats[2]}")
print(f"каналов доступа: {stats[3]}")
print(f"период: {stats[4]} — {stats[5]}")
