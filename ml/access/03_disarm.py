"""Сравнивает две версии метки доступа: со всеми тревогами и без тревог внутри
окон «Снято с охраны».

Канал типа «Состояние охраны» пишет два осмысленных значения: «На охране» и
«Снято с охраны». Канал стоит на объекте целиком, по одному на объект, поэтому
окно накрывает весь объект, а не отдельный пикет. Разбор источника лежит в
`docs/customer/smvu-report.md` §2.

Скрипт пишет три файла:

- `out/disarm_windows.parquet` — окна «снято с охраны» по объектам. Файл нужен
  признаку «участок снят с охраны» из `features.py`;
- `out/access_hourly_armed.parquet` — витрина версии Б, то есть часы с
  тревогой вне окон;
- `out/disarm_stats.json` — замер обеих версий.

Решение по итогу замера записано в `backend/docs/adr/0001-access-target.md`.
"""
import json
import pathlib
import time

import duckdb
import target_stats

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = ROOT / "eda" / "out" / "events.parquet"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "out"
UNITS = OUT / "access_units.parquet"
OUT_WINDOWS = OUT / "disarm_windows.parquet"
OUT_HOURLY_B = OUT / "access_hourly_armed.parquet"
STATS = OUT / "disarm_stats.json"

ACCESS_VALUES = ("Не замкнут", "Обнаружено движение", "Рычаг сдернут")
GUARD_TYPE = "Состояние охраны"
GUARD_OFF = "Снято с охраны"
GUARD_ON = "На охране"
ARTIFACT_OID = 5343
ARTIFACT_YEARS = (2020, 2021)
HORIZON_HOURS = 24

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")

# Моменты тревоги доступа. Отбор повторяет `01_dataset.py`: те же значения, тот
# же артефакт, те же единицы с пикетом. Момент это тройка «канал, дата,
# секунда», поэтому пачка тревог в одну секунду весит как одна.
t = time.time()
con.execute(
    f"""
    CREATE TABLE moment AS
    SELECT
        ev.channel_id AS channel_id,
        ch.oid AS object_id,
        ch.gal_key AS gallery,
        ch.picket AS picket,
        ev.d + ev.t AS ts,
        count(*) AS n_rows
    FROM read_parquet('{EVENTS.as_posix()}') ev
    JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
    WHERE ev.alarm
      AND ev.value IN {ACCESS_VALUES}
      AND ch.picket IS NOT NULL
      AND NOT (ch.oid = {ARTIFACT_OID} AND year(ev.d) IN {ARTIFACT_YEARS})
    GROUP BY 1, 2, 3, 4, 5
    """
)
print(f"моменты тревоги отобраны за {time.time() - t:.0f} c")

# Записи режима охраны. Мусор отсеивается перечнем значений: тот же канал пишет
# несброшенные часы `01.01.1970` и прочие строки.
t = time.time()
con.execute(
    f"""
    CREATE TABLE guard AS
    SELECT ch.oid AS object_id, ev.d + ev.t AS ts, ev.value AS value
    FROM read_parquet('{EVENTS.as_posix()}') ev
    JOIN read_parquet('{CHANNELS.as_posix()}') ch ON ch.cid = ev.channel_id
    WHERE ch.stype = '{GUARD_TYPE}'
      AND ev.value IN ('{GUARD_ON}', '{GUARD_OFF}')
    """
)
print(f"режим охраны отобран за {time.time() - t:.0f} c")

# Окно открывается сменой режима на «снято» и закрывается следующей сменой на
# «на охране». Повторы одного значения подряд ничего не меняют, поэтому сначала
# они схлопываются, а уже потом берётся следующая запись.
con.execute(
    """
    CREATE TABLE step AS
    SELECT object_id, ts, value FROM (
        SELECT object_id, ts, value,
               lag(value) OVER (PARTITION BY object_id ORDER BY ts, value) AS prev
        FROM guard
    )
    WHERE prev IS NULL OR prev <> value
    """
)
con.execute(
    f"""
    CREATE TABLE win AS
    SELECT object_id, t_from, coalesce(t_to, seen_to) AS t_to, t_to IS NULL AS open_end
    FROM (
        SELECT object_id, ts AS t_from, value,
               lead(ts) OVER (PARTITION BY object_id ORDER BY ts) AS t_to
        FROM step
    ) s
    JOIN (SELECT object_id AS oid, max(ts) AS seen_to FROM guard GROUP BY 1)
      ON oid = s.object_id
    WHERE value = '{GUARD_OFF}'
    """
)
con.execute(
    f"""
    COPY (SELECT object_id, t_from, t_to, open_end FROM win ORDER BY 1, 2)
    TO '{OUT_WINDOWS.as_posix()}' (FORMAT PARQUET)
    """
)

# Замер самих окон. Доля времени считается по объектам с каналом режима: на
# остальных режим неизвестен, и делить там нечего.
row = con.execute(
    """
    SELECT
        (SELECT count(*) FROM win),
        (SELECT count(*) FROM win WHERE open_end),
        (SELECT median(date_diff('minute', t_from, t_to)) FROM win),
        (SELECT max(date_diff('hour', t_from, t_to)) FROM win),
        (SELECT sum(date_diff('second', t_from, t_to)) FROM win),
        (SELECT sum(date_diff('second', seen_from, seen_to)) FROM (
            SELECT object_id, min(ts) AS seen_from, max(ts) AS seen_to
            FROM guard GROUP BY 1)),
        (SELECT count(DISTINCT object_id) FROM guard)
    """
).fetchone()
windows = {
    "windows": int(row[0]),
    "open_end": int(row[1]),
    "median_minutes": round(float(row[2]), 1),
    "max_hours": int(row[3]),
    "time_off_pct": round(100.0 * row[4] / row[5], 3),
    "objects_covered": int(row[6]),
}
print(
    f"окон: {windows['windows']}, медиана {windows['median_minutes']} мин, "
    f"доля времени «снято» {windows['time_off_pct']} %, "
    f"объектов с каналом режима {windows['objects_covered']}"
)

# Покрытие и доля тревог внутри окон. Тревога на объекте без канала режима
# остаётся в обеих версиях метки: режим там неизвестен, а выбрасывать событие
# по незнанию нельзя.
con.execute(
    """
    CREATE TABLE moment_flag AS
    SELECT m.*,
        m.object_id IN (SELECT DISTINCT object_id FROM guard) AS covered,
        exists(SELECT 1 FROM win w
               WHERE w.object_id = m.object_id
                 AND m.ts >= w.t_from AND m.ts < w.t_to) AS off_guard
    FROM moment m
    """
)
row = con.execute(
    """
    SELECT
        count(*),
        count(*) FILTER (covered),
        count(*) FILTER (off_guard),
        count(DISTINCT channel_id),
        count(DISTINCT channel_id) FILTER (covered)
    FROM moment_flag
    """
).fetchone()
coverage = {
    "moments": int(row[0]),
    "moments_covered": int(row[1]),
    "moments_off_guard": int(row[2]),
    "channels": int(row[3]),
    "channels_covered": int(row[4]),
    "off_guard_pct": round(100.0 * row[2] / row[0], 3),
    "off_guard_covered_pct": round(100.0 * row[2] / row[1], 3),
    "channels_covered_pct": round(100.0 * row[4] / row[3], 3),
}
print(
    f"моментов: {coverage['moments']}, под каналом режима "
    f"{coverage['moments_covered']}, внутри окон {coverage['moments_off_guard']} "
    f"({coverage['off_guard_pct']} % от всех, "
    f"{coverage['off_guard_covered_pct']} % от покрытых)"
)

# Ночная доля. Она проверяет чтение окна как рабочей смены: работа подрядчика
# идёт днём, а проникновение ночью. Ночь это часы с 22 до 6.
row = con.execute(
    """
    SELECT
        count(*) FILTER (off_guard AND (hour(ts) >= 22 OR hour(ts) < 6)),
        count(*) FILTER (off_guard),
        count(*) FILTER (NOT off_guard AND (hour(ts) >= 22 OR hour(ts) < 6)),
        count(*) FILTER (NOT off_guard)
    FROM moment_flag
    """
).fetchone()
coverage["night_pct_off_guard"] = round(100.0 * row[0] / row[1], 3)
coverage["night_pct_armed"] = round(100.0 * row[2] / row[3], 3)
print(
    f"ночная доля: внутри окон {coverage['night_pct_off_guard']} %, "
    f"вне окон {coverage['night_pct_armed']} %"
)

# Витрина версии Б. Часы считаются по тем же правилам, что в `01_dataset.py`,
# но моменты внутри окон в неё не идут.
con.execute(
    f"""
    COPY (
        SELECT
            object_id,
            gallery,
            picket,
            date_trunc('hour', ts) AS hour,
            sum(n_rows) AS n_alarms,
            count(*) AS n_moments,
            count(DISTINCT channel_id) AS n_channels
        FROM moment_flag
        WHERE NOT off_guard
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    ) TO '{OUT_HOURLY_B.as_posix()}' (FORMAT PARQUET)
    """
)

# Версия А строится тем же запросом без условия. Так обе версии выходят из
# одного кода, и разница в числах идёт только от окон.
con.execute(
    """
    CREATE VIEW alarm_all AS
    SELECT object_id, gallery, picket, date_trunc('hour', ts) AS hour,
           count(*) AS n_moments, count(DISTINCT channel_id) AS n_channels
    FROM moment_flag GROUP BY 1, 2, 3, 4
    """
)
con.execute(
    """
    CREATE VIEW alarm_armed AS
    SELECT object_id, gallery, picket, date_trunc('hour', ts) AS hour,
           count(*) AS n_moments, count(DISTINCT channel_id) AS n_channels
    FROM moment_flag WHERE NOT off_guard GROUP BY 1, 2, 3, 4
    """
)
target_stats.attach(con, UNITS, "unit")


def version(name: str, alarm: str) -> dict:
    row = con.execute(
        f"""
        SELECT count(*), sum(n_moments),
               count(DISTINCT (object_id, gallery, picket))
        FROM {alarm}
        """
    ).fetchone()
    out = {
        "name": name,
        "alarm_hours": int(row[0]),
        "alarm_moments": int(row[1]),
        "units_with_event": int(row[2]),
        "hourly": target_stats.hourly_stats(con, HORIZON_HOURS, alarm=alarm),
    }
    print(
        f"{name}: часов {out['alarm_hours']}, моментов {out['alarm_moments']}, "
        f"единиц с событием {out['units_with_event']}"
    )
    print(f"  {target_stats.line(out['hourly'])}")
    return out


t = time.time()
result = {
    "horizon_hours": HORIZON_HOURS,
    "units": con.execute("SELECT count(*) FROM unit").fetchone()[0],
    "windows": windows,
    "coverage": coverage,
    "version_a": version("версия А, все тревоги", "alarm_all"),
    "version_b": version("версия Б, тревоги вне окон", "alarm_armed"),
}
a = result["version_a"]["hourly"]
b = result["version_b"]["hourly"]
result["delta"] = {
    "base_pct": round(b["base_pct"] - a["base_pct"], 3),
    "base_ratio": round(b["base_pct"] / a["base_pct"], 3),
    "naive_precision_pct": round(b["naive_precision_pct"] - a["naive_precision_pct"], 3),
    "naive_recall_pct": round(b["naive_recall_pct"] - a["naive_recall_pct"], 3),
    "units_with_event": (
        result["version_b"]["units_with_event"]
        - result["version_a"]["units_with_event"]
    ),
}
print(f"обе версии измерены за {time.time() - t:.0f} c")

STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"замер записан в {STATS}")
