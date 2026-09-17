"""Считает признаки направления «несанкционированный доступ» на момент расчёта
`at`. Признаки строятся по `out/access_hourly.parquet`, а не по
`out/access_hourly_armed.parquet`: метку решение ADR
`backend/docs/adr/0001-access-target.md` (раздел «Что из этого следует») велит
чистить от тревог подрядчика, а признакам эти тревоги оставить. Тревога внутри
окна «Снято с охраны» не проникновение для метки, но для признака это всё
равно активность на месте, и выбрасывать её значит терять сигнал о частоте
событий на пикете.

Разбор двенадцати признаков и решения по неоднозначным формулировкам
backlog-строки T05/T06 (`loop/BACKLOG.md`):

- `alarm_hour_share_720h` считает долю тревожных часов за 30 суток, а не долю
  тревожных записей среди всех: `access_hourly` держит только строки с
  тревогой, и второе число из него не достать. Формула повторяет признак
  `duty` (`active_days / lifetime_days`) из `notebooks/01-eda-clustering.ipynb`,
  только на часовой сетке вместо суточной.
- `hours_since_last_alarm` для единицы без тревог в истории берёт не 0 и не
  бесконечность, а `hour_from` из `access_units`: это число часов, что мы
  наблюдаем единицу, и оно всегда определено.
- `night_share` считает ночную долю по всей истории тревог единицы до `at`,
  без окна в 720 часов. Формула и граница ночи (22–6 часов) повторяют признак
  «ночная доля» из `03_disarm.py`. Единица без истории получает 0, а не 0,5:
  это документированное значение по умолчанию, не оценка «пополам».
- `day_of_week` следует соглашению DuckDB: 0 это воскресенье, 6 это суббота
  (проверено запросом `dayofweek()` на известных датах).
- `neighbor_channels_1h` берёт `max(n_channels)` за последний завершённый час:
  `n_channels` в `access_hourly` уже число каналов пикета в тревоге за час, и
  агрегат `max` по одной строке ничего не меняет, но защищает от случая, когда
  часовой свод склеит несколько строк на одну единицу и час.

Каждый фильтр обязан читать `hour < at`, никогда `hour <= at`: признак не
имеет права видеть час расчёта и позже, иначе прогноз смотрит в будущее.
"""
import json
import pathlib
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent / "out"
HOURLY = OUT / "access_hourly.parquet"
UNITS = OUT / "access_units.parquet"
ARMED = OUT / "access_hourly_armed.parquet"
OUT_FEATURES = OUT / "access_features.parquet"
STATS = OUT / "features_stats.json"

KEY = "object_id, gallery, picket"
HORIZON_HOURS = 24
# Доля отрицательных клеток, что остаётся в обучающей панели. Полная сетка
# «единица и час» держит 62,6 млн клеток (ADR 0001), и обучать модель на всех
# них дорого без выигрыша в качестве.
NEGATIVE_KEEP_RATE = 0.02

FEATURE_COLUMNS = [
    "n_alarms_1h",
    "n_alarms_24h",
    "n_alarms_168h",
    "n_alarms_720h",
    "alarm_hour_share_720h",
    "hours_since_last_alarm",
    "night_share",
    "hour_of_day",
    "day_of_week",
    "month",
    "is_weekend",
    "neighbor_channels_1h",
]


def attach_sources(
    con: duckdb.DuckDBPyConnection,
    hourly: pathlib.Path = HOURLY,
    units: pathlib.Path = UNITS,
) -> None:
    """Подключает часовой свод и границы жизни единиц как представления
    `alarm` и `unit`."""
    con.execute(
        f"CREATE OR REPLACE VIEW alarm AS "
        f"SELECT * FROM read_parquet('{hourly.as_posix()}')"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW unit AS "
        f"SELECT * FROM read_parquet('{units.as_posix()}')"
    )


def build_features(
    con: duckdb.DuckDBPyConnection,
    points: str = "points",
    alarm: str = "alarm",
    unit: str = "unit",
) -> str:
    """Строит представление `features` над точками `points(object_id, gallery,
    picket, at)`. Каждое окно строго до `at`, час расчёта в него не входит.
    """
    con.execute(
        f"""
        CREATE OR REPLACE VIEW features_raw AS
        SELECT
            p.object_id,
            p.gallery,
            p.picket,
            p.at,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 1 HOUR AND a.hour < p.at) AS n_alarms_1h,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 24 HOUR AND a.hour < p.at) AS n_alarms_24h,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 168 HOUR AND a.hour < p.at) AS n_alarms_168h,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 720 HOUR AND a.hour < p.at) AS n_alarms_720h,
            (SELECT count(*) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 720 HOUR AND a.hour < p.at) AS alarm_hours_720h,
            (SELECT max(a.hour) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS last_alarm_hour,
            (SELECT u.hour_from FROM {unit} u
             WHERE (u.object_id, u.gallery, u.picket) = (p.object_id, p.gallery, p.picket)) AS hour_from,
            (SELECT sum(a.n_moments) FILTER (hour(a.hour) >= 22 OR hour(a.hour) < 6)
             FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS night_moments,
            (SELECT sum(a.n_moments) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour < p.at) AS past_moments,
            (SELECT max(a.n_channels) FROM {alarm} a
             WHERE (a.object_id, a.gallery, a.picket) = (p.object_id, p.gallery, p.picket)
               AND a.hour > p.at - INTERVAL 1 HOUR AND a.hour < p.at) AS neighbor_channels_1h
        FROM {points} p
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW features AS
        SELECT
            object_id,
            gallery,
            picket,
            "at",
            coalesce(n_alarms_1h, 0) AS n_alarms_1h,
            coalesce(n_alarms_24h, 0) AS n_alarms_24h,
            coalesce(n_alarms_168h, 0) AS n_alarms_168h,
            coalesce(n_alarms_720h, 0) AS n_alarms_720h,
            coalesce(alarm_hours_720h, 0) / 720.0 AS alarm_hour_share_720h,
            date_diff(
                'hour',
                coalesce(last_alarm_hour, hour_from),
                "at"
            ) AS hours_since_last_alarm,
            coalesce(night_moments, 0) / greatest(coalesce(past_moments, 0), 1) AS night_share,
            hour("at") AS hour_of_day,
            dayofweek("at") AS day_of_week,
            month("at") AS month,
            CAST(dayofweek("at") IN (0, 6) AS INTEGER) AS is_weekend,
            coalesce(neighbor_channels_1h, 0) AS neighbor_channels_1h
        FROM features_raw
        """
    )
    return "features"


if __name__ == "__main__":
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    con.execute("SELECT setseed(0.4217)")

    attach_sources(con)
    con.execute(
        f"CREATE OR REPLACE VIEW armed AS "
        f"SELECT * FROM read_parquet('{ARMED.as_posix()}')"
    )

    # Положительные точки: те же 24 часа перед каждым часом с тревогой в
    # версии Б метки, что и в `target_stats.hourly_stats`, обрезанные по жизни
    # единицы.
    t = time.time()
    con.execute(
        f"""
        CREATE TABLE positive AS
        SELECT DISTINCT p.object_id, p.gallery, p.picket, p.hour AS at
        FROM (
            SELECT {KEY}, hour - (s.i * INTERVAL 1 HOUR) AS hour
            FROM armed, generate_series(1, {HORIZON_HOURS}) s(i)
        ) p
        JOIN unit u USING ({KEY})
        WHERE p.hour BETWEEN u.hour_from AND u.hour_to
        """
    )
    n_positive = con.execute("SELECT count(*) FROM positive").fetchone()[0]
    print(f"положительных точек: {n_positive}, посчитано за {time.time() - t:.0f} c")

    # Отрицательные точки: случайные часы жизни каждой единицы, доля
    # `NEGATIVE_KEEP_RATE` от длины жизни. Столкновения с положительным
    # множеством убираются анти-join, чтобы точка не несла обе метки.
    t = time.time()
    con.execute(
        f"""
        CREATE TABLE negative_raw AS
        SELECT
            {KEY},
            hour_from + (
                (random() * date_diff('hour', hour_from, hour_to))::BIGINT * INTERVAL 1 HOUR
            ) AS at
        FROM unit, generate_series(
            1, CAST(ceil(
                (date_diff('hour', hour_from, hour_to) + 1) * {NEGATIVE_KEEP_RATE}
            ) AS BIGINT)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE negative AS
        SELECT DISTINCT n.object_id, n.gallery, n.picket, n.at
        FROM negative_raw n
        ANTI JOIN positive p ON (n.object_id, n.gallery, n.picket, n.at)
                              = (p.object_id, p.gallery, p.picket, p.at)
        """
    )
    n_negative = con.execute("SELECT count(*) FROM negative").fetchone()[0]
    print(f"отрицательных точек: {n_negative}, посчитано за {time.time() - t:.0f} c")

    con.execute(
        """
        CREATE TABLE points AS
        SELECT object_id, gallery, picket, "at", 1 AS label FROM positive
        UNION ALL
        SELECT object_id, gallery, picket, "at", 0 AS label FROM negative
        """
    )

    t = time.time()
    build_features(con)
    con.execute(
        f"""
        COPY (
            SELECT f.*, pt.label
            FROM features f
            JOIN points pt USING (object_id, gallery, picket, "at")
            ORDER BY object_id, gallery, picket, "at"
        ) TO '{OUT_FEATURES.as_posix()}' (FORMAT PARQUET)
        """
    )
    print(f"признаки посчитаны за {time.time() - t:.0f} c")

    stats = con.execute(
        f"""
        SELECT
            count(*),
            count(*) FILTER (label = 1),
            count(*) FILTER (label = 0),
            count(DISTINCT (object_id, gallery, picket)),
            min(night_share),
            max(night_share),
            min(alarm_hour_share_720h),
            max(alarm_hour_share_720h),
            min(hours_since_last_alarm)
        FROM (
            SELECT f.*, pt.label
            FROM read_parquet('{OUT_FEATURES.as_posix()}') f
            JOIN points pt USING (object_id, gallery, picket, "at")
        )
        """
    ).fetchone()
    result = {
        "rows": int(stats[0]),
        "positive_rows": int(stats[1]),
        "negative_rows": int(stats[2]),
        "negative_keep_rate": NEGATIVE_KEEP_RATE,
        "units": int(stats[3]),
        "night_share_range": [float(stats[4]), float(stats[5])],
        "alarm_hour_share_720h_range": [float(stats[6]), float(stats[7])],
        "hours_since_last_alarm_min": int(stats[8]),
    }
    STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"строк: {result['rows']}, положительных: {result['positive_rows']}, "
        f"отрицательных: {result['negative_rows']}, единиц: {result['units']}"
    )
    print(f"замер записан в {STATS}")
