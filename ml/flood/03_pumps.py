"""Сводит работу насосов к часам: сырьё признаков насоса для `04_panel.py`.

Насос пишет в журнал смены состояния, а не опрос: 90 % записей «Включен» и
«Выключен» меняют значение, медианный интервал между ними 247 секунд. Поэтому:

- **число смен за час** считается по записям, которые меняют значение;
- **минуты во включённом состоянии** считаются по интервалам: насос включён от
  записи «Включен» до следующей записи «Включен» или «Выключен» того же канала.
  Интервал длиннее суток обрезается до суток: за таким разрывом лежит пропуск
  данных, а не сутки работы.

Кроме работы насоса скрипт считает по часам три тревоги того же канала:
недоступность («Обесточен», «Неисправен», «Отключено устройство»), «Работают
все насосы в АНС» и «Затоплен». Последняя здесь нужна только для признаков
объекта: насосы без пикета в метку не входят (ADR 0008), но их затопления
видны соседям.

В свод идут и насосы без пикета. Единицы у них нет, но объект и комплекс есть,
а признаки соседства считаются по объекту и комплексу.

Выход: `out/pump_hourly.parquet` и `out/pump_channels.parquet`."""

import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "eda" / "out" / "channels.parquet"
OBJECTS = ROOT / "raw_task" / "dataset" / "справочник_объектов_диспетчер.csv"
OUT = pathlib.Path(__file__).resolve().parent / "out"
OUT_HOURLY = OUT / "pump_hourly.parquet"
OUT_CHANNELS = OUT / "pump_channels.parquet"

UNAVAILABLE = ("Обесточен", "Неисправен", "Отключено устройство")
ALL_PUMPS = "Работают все насосы в АНС"
FLOODED = "Затоплен"
MAX_INTERVAL_MINUTES = 24 * 60
ARTIFACT_OID = 5343
ARTIFACT_YEARS = (2020, 2021)

OUT.mkdir(exist_ok=True)
con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")

con.execute(
    f"""
    CREATE TABLE pump_channel AS
    SELECT ch.cid AS channel_id, ch.oid AS object_id, ch.gal_key AS gallery,
           ch.picket AS picket, CAST(o."родитель" AS BIGINT) AS complex_id
    FROM read_parquet('{CHANNELS.as_posix()}') ch
    JOIN read_csv('{OBJECTS.as_posix()}', header = true, all_varchar = true) o
      ON CAST(o."ид_объект" AS BIGINT) = ch.oid
    WHERE ch.stype = 'Состояние насоса'
    """
)
con.execute(f"COPY pump_channel TO '{OUT_CHANNELS.as_posix()}' (FORMAT PARQUET)")

t = time.time()
con.execute(
    f"""
    CREATE TABLE pump_event AS
    SELECT ev.channel_id, ev.d + ev.t AS ts, ev.value, ev.alarm
    FROM read_parquet('{EVENTS.as_posix()}') ev
    JOIN pump_channel p ON p.channel_id = ev.channel_id
    WHERE NOT (p.object_id = {ARTIFACT_OID} AND year(ev.d) IN {ARTIFACT_YEARS})
    """
)
print(f"записи насосов отобраны за {time.time() - t:.0f} c")

# Смены состояния и интервалы работы.
t = time.time()
con.execute(
    """
    CREATE TABLE state AS
    SELECT channel_id, ts, value,
           lag(value) OVER w AS prev_value,
           lead(ts) OVER w AS next_ts
    FROM pump_event
    WHERE value IN ('Включен', 'Выключен')
    WINDOW w AS (PARTITION BY channel_id ORDER BY ts, value)
    """
)
con.execute(
    """
    CREATE TABLE changes AS
    SELECT channel_id, date_trunc('hour', ts) AS hour, count(*) AS n_changes
    FROM state
    WHERE prev_value IS NOT NULL AND value <> prev_value
    GROUP BY ALL
    """
)
# Интервал «включён» режется по границам часов.
con.execute(
    f"""
    CREATE TABLE on_minutes AS
    WITH iv AS (
        SELECT channel_id, ts AS a,
               least(coalesce(next_ts, ts), ts + INTERVAL {MAX_INTERVAL_MINUTES} MINUTE) AS b
        FROM state WHERE value = 'Включен'
    ),
    cut AS (
        SELECT iv.channel_id, h.hour,
               greatest(iv.a, h.hour) AS a,
               least(iv.b, h.hour + INTERVAL 1 HOUR) AS b
        FROM iv,
             LATERAL (SELECT unnest(generate_series(date_trunc('hour', iv.a),
                                                    date_trunc('hour', iv.b),
                                                    INTERVAL 1 HOUR)) AS hour) h
        WHERE iv.b > iv.a
    )
    SELECT channel_id, hour, sum(date_diff('second', a, b)) / 60.0 AS on_minutes
    FROM cut WHERE b > a GROUP BY ALL
    """
)
con.execute(
    f"""
    CREATE TABLE alarms AS
    SELECT channel_id, date_trunc('hour', ts) AS hour,
           count(DISTINCT ts) FILTER (WHERE value IN {UNAVAILABLE}) AS n_unavail,
           count(DISTINCT ts) FILTER (WHERE value = '{ALL_PUMPS}') AS n_all_pumps,
           count(DISTINCT ts) FILTER (WHERE value = '{FLOODED}') AS n_flooded
    FROM pump_event
    WHERE alarm AND value IN ({", ".join(f"'{v}'" for v in UNAVAILABLE)},
                              '{ALL_PUMPS}', '{FLOODED}')
    GROUP BY ALL
    """
)
print(f"смены, интервалы и тревоги посчитаны за {time.time() - t:.0f} c")

t = time.time()
con.execute(
    f"""
    COPY (
        SELECT p.channel_id, p.object_id, p.gallery, p.picket, p.complex_id, x.hour,
               coalesce(c.n_changes, 0) AS n_changes,
               coalesce(m.on_minutes, 0) AS on_minutes,
               coalesce(a.n_unavail, 0) AS n_unavail,
               coalesce(a.n_all_pumps, 0) AS n_all_pumps,
               coalesce(a.n_flooded, 0) AS n_flooded
        FROM (SELECT channel_id, hour FROM changes
              UNION SELECT channel_id, hour FROM on_minutes
              UNION SELECT channel_id, hour FROM alarms) x
        JOIN pump_channel p USING (channel_id)
        LEFT JOIN changes c USING (channel_id, hour)
        LEFT JOIN on_minutes m USING (channel_id, hour)
        LEFT JOIN alarms a USING (channel_id, hour)
        ORDER BY p.channel_id, x.hour
    ) TO '{OUT_HOURLY.as_posix()}' (FORMAT PARQUET)
    """
)
print(f"свод записан за {time.time() - t:.0f} c")

row = con.execute(
    f"""
    SELECT count(*), count(DISTINCT channel_id),
           count(DISTINCT channel_id) FILTER (WHERE picket IS NULL),
           sum(n_changes), sum(on_minutes) / 60, sum(n_unavail), sum(n_all_pumps),
           sum(n_flooded)
    FROM read_parquet('{OUT_HOURLY.as_posix()}')
    """
).fetchone()
print(f"строк «канал и час»: {row[0]}, каналов {row[1]}, из них без пикета {row[2]}")
print(f"смен состояния {row[3]}, часов работы {row[4]:.0f}")
print(f"моментов: недоступность {row[5]}, все насосы {row[6]}, затоплен {row[7]}")
