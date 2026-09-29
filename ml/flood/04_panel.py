"""Панель признаков направления «риск подтопления».

**Сетка.** Строка это «единица и час». Точка расчёта стоит в начале часа `h`,
все признаки считаются строго по часам до `h`. Суточная сетка это строки с
`h` в 00:00: признаки у двух сеток общие, и сравнение сеток меряет только шаг
прогноза, а не разницу в признаках.

**Метка** (ADR 0012). Единица имеет воду в часах `h … h + 23`. Вода это сигнал
«Затоплен» насоса или «Не замкнут» датчика затопления в сутках, которые
классификатор `11_pu_label.py` признал водой, а не плановой проверкой. Строки,
у которых окно метки выходит за конец жизни единицы, выбывают.

**Наивная планка.** Событие было в часах `h − 24 … h − 1`. Для суточной сетки
это «событие было вчера».

**Признаки** идут группами в порядке цели:

1. `pump_*` — насосы единицы: смены состояния, мигание, доля времени работы и
   её рост, недоступность, «Работают все насосы в АНС».
2. `evt_*` — история событий самой единицы.
3. `obj_*`, `cx_*` — те же величины у соседей: остальные единицы и насосы без
   пикета того же объекта, остальные объекты того же комплекса. Уклонов нет,
   поэтому соседство задано деревом объектов (разуклонка, QA-сессия).
4. `cal_*` — сезон и календарь. Признаки дня недели, нерабочего дня,
   праздника и часа в панели есть, но в модель не идут: модель ищет воду, а
   не график работ (ADR 0012).
5. `check_*` — сигналы в сутках, которые классификатор признал плановой
   проверкой (ADR 0012).
6. `weather_*` — погода Москвы до точки расчёта: осадки, снег, таяние,
   температура (`08_weather.py`).
7. `recent_*` — вода, проверки и откачка за последние 6 и 12 часов.

**Мигание.** Порог не назначен числом. Это квантиль 0,99 числа смен за час у
исправных насосов до первого проверочного года: час насоса, в сутки которого
не было ни недоступности, ни затопления. Порог считается только по этим годам,
иначе он подсмотрел бы проверку.

Окна считаются оконными функциями по плотной сетке, поэтому `ROWS BETWEEN n
PRECEDING AND 1 PRECEDING` совпадает с календарным окном в часах.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/flood/04_panel.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))

import calendar_ru

ROOT = HERE.parents[1]
CHANNELS = ROOT / "eda" / "out" / "channels.parquet"
OBJECTS = ROOT / "raw_task" / "dataset" / "справочник_объектов_диспетчер.csv"
OUT = HERE / "out"
HOURLY = OUT / "flood_hourly.parquet"
UNITS = OUT / "flood_units.parquet"
PUMP_HOURLY = OUT / "pump_hourly.parquet"
PUMP_CHANNELS = OUT / "pump_channels.parquet"
WEATHER = OUT / "weather_hourly.parquet"
PU_EVENTS = OUT / "pu_events.parquet"
PANEL = OUT / "panel_hourly.parquet"
STATS = OUT / "panel_stats.json"

KEY = "object_id, gallery, picket"
LABEL_VALUES = ("Затоплен", "Не замкнут")
# Порог мигания считается по годам до первого проверочного года (`10_train.py`).
THRESHOLD_END = "2022-07-01"
BLINK_QUANTILE = 0.99
HORIZON = 24


def blink_threshold(con: duckdb.DuckDBPyConnection) -> float:
    """Квантиль числа смен за час у исправных насосов на обучающем отрезке."""
    row = con.execute(
        f"""
        WITH bad_day AS (
            SELECT DISTINCT channel_id, hour::DATE AS day FROM pump
            WHERE n_unavail > 0 OR n_flooded > 0
        )
        SELECT quantile_cont(p.n_changes, {BLINK_QUANTILE})
        FROM pump p
        LEFT JOIN bad_day b ON b.channel_id = p.channel_id AND b.day = p.hour::DATE
        WHERE p.hour < TIMESTAMP '{THRESHOLD_END}' AND p.n_changes > 0 AND b.day IS NULL
        """
    ).fetchone()
    return float(row[0])


def _roll(column: str, hours: int, name: str, part: str = KEY) -> str:
    """Сумма по окну прошлых часов, текущий час не входит."""
    return (
        f"sum({column}) OVER (PARTITION BY {part} ORDER BY hour "
        f"ROWS BETWEEN {hours} PRECEDING AND 1 PRECEDING) AS {name}"
    )


def build_unit_hours(con: duckdb.DuckDBPyConnection, blink: float) -> None:
    """Плотная сетка «единица и час» с суммами за час."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE unit_hour AS
        WITH grid AS (
            SELECT u.object_id, u.gallery, u.picket, h.hour
            FROM unit u,
                 LATERAL (SELECT unnest(generate_series(u.hour_from, u.hour_to,
                                                        INTERVAL 1 HOUR)) AS hour) h
        ),
        evt AS (
            SELECT {KEY}, hour, sum(n_moments) AS m_evt FROM alarm GROUP BY ALL
        ),
        chk AS (
            SELECT {KEY}, hour, count(*) AS m_check FROM check_signal GROUP BY ALL
        ),
        pmp AS (
            SELECT {KEY}, hour,
                   sum(n_changes) AS chg,
                   sum(CAST(n_changes > {blink} AS INTEGER)) AS blink,
                   sum(on_minutes) AS on_min,
                   sum(n_unavail) AS unavail,
                   sum(n_all_pumps) AS allp
            FROM pump WHERE picket IS NOT NULL GROUP BY ALL
        )
        SELECT g.object_id, g.gallery, g.picket, g.hour,
               coalesce(e.m_evt, 0) AS m_evt,
               CAST(e.m_evt IS NOT NULL AS INTEGER) AS had_evt,
               coalesce(p.chg, 0) AS chg,
               coalesce(p.blink, 0) AS blink,
               coalesce(p.on_min, 0) AS on_min,
               coalesce(p.unavail, 0) AS unavail,
               coalesce(p.allp, 0) AS allp,
               CAST(c.m_check IS NOT NULL AS INTEGER) AS had_check
        FROM grid g
        LEFT JOIN evt e USING ({KEY}, hour)
        LEFT JOIN pmp p USING ({KEY}, hour)
        LEFT JOIN chk c USING ({KEY}, hour)
        """
    )


def build_group_hours(
    con: duckdb.DuckDBPyConnection, blink: float, calendar: str
) -> None:
    """Суммы за час по объекту и по комплексу.

    Объект включает насосы без пикета: у них нет единицы, но их затопления и
    работа видны соседям. События единиц идут из метки, затопления насосов без
    пикета из свода насосов, так одно событие не считается дважды. Затопления
    насосов без пикета классифицировать нечем: у них нет суток пикета. Поэтому
    водой соседей они не считаются, а их работа насоса остаётся признаком.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE object_hour_raw AS
        SELECT object_id, hour,
               sum(m_evt) AS m_evt, sum(had_evt) AS units_evt,
               sum(chg) AS chg, sum(blink) AS blink, sum(on_min) AS on_min,
               sum(unavail) AS unavail, sum(allp) AS allp
        FROM (
            SELECT object_id, hour, m_evt, had_evt, 0 AS chg, 0 AS blink, 0 AS on_min,
                   0 AS unavail, 0 AS allp
            FROM unit_hour WHERE had_evt = 1
            UNION ALL
            SELECT p.object_id, p.hour,
                   0, 0,
                   p.n_changes, CAST(p.n_changes > {blink} AS INTEGER), p.on_minutes,
                   p.n_unavail, p.n_all_pumps
            FROM pump p JOIN {calendar} k ON k.day = p.hour::DATE
            WHERE p.picket IS NULL
            UNION ALL
            SELECT object_id, hour, 0, 0, n_changes,
                   CAST(n_changes > {blink} AS INTEGER), on_minutes, n_unavail,
                   n_all_pumps
            FROM pump WHERE picket IS NOT NULL
        )
        GROUP BY ALL
        """
    )
    for level, key, source in (
        ("object", "object_id", "object_hour_raw"),
        ("complex", "complex_id", "complex_hour_raw"),
    ):
        if level == "complex":
            con.execute(
                """
                CREATE OR REPLACE TABLE complex_hour_raw AS
                SELECT c.complex_id, r.hour,
                       sum(m_evt) AS m_evt, sum(units_evt) AS units_evt,
                       sum(chg) AS chg, sum(blink) AS blink, sum(on_min) AS on_min,
                       sum(unavail) AS unavail, sum(allp) AS allp
                FROM object_hour_raw r JOIN object_complex c USING (object_id)
                GROUP BY ALL
                """
            )
        # Плотная сетка по группе: от первого до последнего часа любой единицы.
        span_source = (
            "SELECT object_id AS g, hour_from, hour_to FROM unit"
            if level == "object"
            else "SELECT c.complex_id AS g, u.hour_from, u.hour_to "
            "FROM unit u JOIN object_complex c USING (object_id)"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {level}_rolled AS
            WITH span AS (
                SELECT g, min(hour_from) AS a, max(hour_to) AS b
                FROM ({span_source}) GROUP BY g
            ),
            grid AS (
                SELECT span.g AS {key}, h.hour
                FROM span,
                     LATERAL (SELECT unnest(generate_series(span.a, span.b,
                                                            INTERVAL 1 HOUR)) AS hour) h
            ),
            dense AS (
                SELECT g.{key}, g.hour,
                       coalesce(r.m_evt, 0) AS m_evt,
                       coalesce(r.units_evt, 0) AS units_evt,
                       coalesce(r.chg, 0) AS chg, coalesce(r.blink, 0) AS blink,
                       coalesce(r.on_min, 0) AS on_min,
                       coalesce(r.unavail, 0) AS unavail, coalesce(r.allp, 0) AS allp
                FROM grid g LEFT JOIN {source} r USING ({key}, hour)
            )
            SELECT {key}, hour,
                {_roll("m_evt", 24, "m_evt_24h", key)},
                {_roll("m_evt", 168, "m_evt_168h", key)},
                {_roll("units_evt", 24, "units_evt_24h", key)},
                {_roll("chg", 24, "chg_24h", key)},
                {_roll("blink", 24, "blink_24h", key)},
                {_roll("on_min", 24, "on_min_24h", key)},
                {_roll("unavail", 24, "unavail_24h", key)},
                {_roll("allp", 24, "allp_24h", key)},
                {_roll("allp", 168, "allp_168h", key)}
            FROM dense
            """
        )


def build_weather(con: duckdb.DuckDBPyConnection) -> None:
    """Погода до начала часа: окна прошлых часов, текущий час не входит."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE weather_rolled AS
        SELECT hour,
            {_roll("precip", 24, "precip_24h", "1")},
            {_roll("precip", 72, "precip_72h", "1")},
            {_roll("precip", 168, "precip_168h", "1")},
            {_roll("rain", 72, "rain_72h", "1")},
            {_roll("snowfall", 72, "snowfall_72h", "1")},
            avg(temp) OVER (ORDER BY hour ROWS BETWEEN 24 PRECEDING AND 1 PRECEDING)
                AS temp_mean_24h,
            max(temp) OVER (ORDER BY hour ROWS BETWEEN 72 PRECEDING AND 1 PRECEDING)
                AS temp_max_72h,
            lag(snow_depth, 1) OVER (ORDER BY hour) AS snow_now,
            lag(snow_depth, 72) OVER (ORDER BY hour) AS snow_72h_ago
        FROM weather
        """
    )


def build_panel(con: duckdb.DuckDBPyConnection, blink: float) -> None:
    calendar_view = calendar_ru.build_calendar(con)
    build_unit_hours(con, blink)
    build_group_hours(con, blink, calendar_view)
    build_weather(con)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE unit_rolled AS
        SELECT object_id, gallery, picket, hour,
            -- метка и планка
            sum(had_evt) OVER (PARTITION BY {KEY} ORDER BY hour
                ROWS BETWEEN CURRENT ROW AND {HORIZON - 1} FOLLOWING) AS evt_next,
            count(*) OVER (PARTITION BY {KEY} ORDER BY hour
                ROWS BETWEEN CURRENT ROW AND {HORIZON - 1} FOLLOWING) AS rows_next,
            {_roll("had_evt", 24, "evt_hours_24h")},
            {_roll("had_evt", 168, "evt_hours_168h")},
            {_roll("had_evt", 720, "evt_hours_720h")},
            {_roll("had_evt", 2160, "evt_hours_2160h")},
            {_roll("had_evt", 8760, "evt_hours_8760h")},
            {_roll("m_evt", 168, "evt_moments_168h")},
            {_roll("m_evt", 24, "m_evt_24h")},
            {_roll("chg", 1, "chg_1h")},
            {_roll("chg", 6, "chg_6h")},
            {_roll("chg", 24, "chg_24h")},
            {_roll("blink", 24, "blink_24h")},
            {_roll("blink", 168, "blink_168h")},
            {_roll("on_min", 24, "on_min_24h")},
            {_roll("on_min", 168, "on_min_168h")},
            {_roll("on_min", 720, "on_min_720h")},
            {_roll("unavail", 24, "unavail_24h")},
            {_roll("unavail", 168, "unavail_168h")},
            {_roll("allp", 24, "allp_24h")},
            {_roll("allp", 168, "allp_168h")},
            {_roll("had_check", 168, "check_hours_168h")},
            {_roll("had_check", 24, "check_hours_24h")},
            {_roll("had_evt", 6, "evt_hours_6h")},
            {_roll("had_evt", 12, "evt_hours_12h")},
            {_roll("on_min", 6, "on_min_6h")},
            {_roll("had_check", 720, "check_hours_720h")},
            max(CASE WHEN had_evt = 1 THEN hour END) OVER (
                PARTITION BY {KEY} ORDER BY hour
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_evt,
            min(hour) OVER (PARTITION BY {KEY}) AS first_hour
        FROM unit_hour
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE panel AS
        SELECT
            r.object_id, r.gallery, r.picket, r.hour,
            CAST(r.evt_next > 0 AS INTEGER) AS label,
            CAST(coalesce(r.evt_hours_24h, 0) > 0 AS INTEGER) AS naive,

            -- 1. насосы единицы
            coalesce(r.chg_1h, 0) AS pump_changes_1h,
            coalesce(r.chg_6h, 0) AS pump_changes_6h,
            coalesce(r.chg_24h, 0) AS pump_changes_24h,
            coalesce(r.blink_24h, 0) AS pump_blink_hours_24h,
            coalesce(r.blink_168h, 0) AS pump_blink_hours_168h,
            coalesce(r.on_min_24h, 0) / (60.0 * 24 * greatest(m.n_pumps, 1))
                AS pump_on_share_24h,
            coalesce(r.on_min_168h, 0) / (60.0 * 168 * greatest(m.n_pumps, 1))
                AS pump_on_share_168h,
            (coalesce(r.on_min_24h, 0) / 24.0)
                / greatest(coalesce(r.on_min_720h, 0) / 720.0, 0.1) AS pump_on_growth_24_720,
            coalesce(r.unavail_24h, 0) AS pump_unavailable_24h,
            coalesce(r.unavail_168h, 0) AS pump_unavailable_168h,
            coalesce(r.allp_24h, 0) AS pump_all_running_24h,
            coalesce(r.allp_168h, 0) AS pump_all_running_168h,

            -- 2. история событий единицы
            coalesce(r.evt_hours_24h, 0) AS evt_hours_24h,
            coalesce(r.evt_hours_168h, 0) AS evt_hours_168h,
            coalesce(r.evt_hours_720h, 0) AS evt_hours_720h,
            coalesce(r.evt_hours_2160h, 0) AS evt_hours_2160h,
            coalesce(r.evt_hours_8760h, 0) AS evt_hours_8760h,
            coalesce(r.evt_moments_168h, 0) AS evt_moments_168h,
            coalesce(date_diff('hour', r.last_evt, r.hour),
                     date_diff('hour', r.first_hour, r.hour) + 1) AS evt_hours_since_last,

            -- 3. соседи: объект без самой единицы, комплекс без своего объекта
            coalesce(o.m_evt_24h, 0) - coalesce(r.m_evt_24h, 0) AS obj_events_24h,
            coalesce(o.m_evt_168h, 0) - coalesce(r.evt_moments_168h, 0) AS obj_events_168h,
            coalesce(o.chg_24h, 0) - coalesce(r.chg_24h, 0) AS obj_pump_changes_24h,
            coalesce(o.blink_24h, 0) - coalesce(r.blink_24h, 0) AS obj_pump_blink_24h,
            coalesce(o.unavail_24h, 0) - coalesce(r.unavail_24h, 0) AS obj_pump_unavailable_24h,
            coalesce(o.allp_24h, 0) - coalesce(r.allp_24h, 0) AS obj_pump_all_running_24h,
            coalesce(o.on_min_24h, 0) - coalesce(r.on_min_24h, 0) AS obj_pump_on_minutes_24h,
            coalesce(c.m_evt_24h, 0) - coalesce(o.m_evt_24h, 0) AS cx_events_24h,
            coalesce(c.m_evt_168h, 0) - coalesce(o.m_evt_168h, 0) AS cx_events_168h,
            coalesce(c.blink_24h, 0) - coalesce(o.blink_24h, 0) AS cx_pump_blink_24h,
            coalesce(c.unavail_24h, 0) - coalesce(o.unavail_24h, 0) AS cx_pump_unavailable_24h,
            coalesce(c.allp_168h, 0) - coalesce(o.allp_168h, 0) AS cx_pump_all_running_168h,

            -- устройство места
            m.n_pumps AS unit_pumps,
            m.n_flood_sensors AS unit_flood_sensors,
            date_diff('day', r.first_hour, r.hour) AS unit_age_days,

            -- 7. свежесть внутри суток: вечер накануне весит больше утра
            coalesce(r.evt_hours_6h, 0) AS recent_evt_hours_6h,
            coalesce(r.evt_hours_12h, 0) AS recent_evt_hours_12h,
            coalesce(r.check_hours_24h, 0) AS recent_check_hours_24h,
            coalesce(r.on_min_6h, 0) / (60.0 * 6 * greatest(m.n_pumps, 1))
                AS recent_pump_on_share_6h,

            -- 5. проверки на пикете
            coalesce(r.check_hours_168h, 0) AS check_hours_168h,
            coalesce(r.check_hours_720h, 0) AS check_hours_720h,

            -- 6. погода до начала часа
            coalesce(w.precip_24h, 0) AS weather_precip_24h,
            coalesce(w.precip_72h, 0) AS weather_precip_72h,
            coalesce(w.precip_168h, 0) AS weather_precip_168h,
            coalesce(w.rain_72h, 0) AS weather_rain_72h,
            coalesce(w.snowfall_72h, 0) AS weather_snowfall_72h,
            w.temp_mean_24h AS weather_temp_mean_24h,
            w.temp_max_72h AS weather_temp_max_72h,
            coalesce(w.snow_now, 0) * 100 AS weather_snow_depth_cm,
            greatest(0, coalesce(w.snow_72h_ago, 0) - coalesce(w.snow_now, 0)) * 100
                AS weather_melt_72h_cm,

            -- 4. календарь
            hour(r.hour) AS cal_hour,
            k.season AS cal_season, k.month AS cal_month, k.day_of_year AS cal_day_of_year,
            k.day_of_week AS cal_day_of_week, k.is_day_off AS cal_is_day_off,
            k.is_holiday AS cal_is_holiday, k.day_off_chain AS cal_day_off_chain
        FROM unit_rolled r
        JOIN unit u USING ({KEY})
        JOIN unit_meta m USING ({KEY})
        JOIN object_complex oc ON oc.object_id = r.object_id
        LEFT JOIN object_rolled o ON o.object_id = r.object_id AND o.hour = r.hour
        LEFT JOIN complex_rolled c ON c.complex_id = oc.complex_id AND c.hour = r.hour
        LEFT JOIN weather_rolled w ON w.hour = r.hour
        JOIN {calendar_view} k ON k.day = r.hour::DATE
        WHERE r.rows_next = {HORIZON} AND r.hour + INTERVAL {HORIZON - 1} HOUR <= u.hour_to
        ORDER BY r.object_id, r.gallery, r.picket, r.hour
        """
    )


def attach_sources(con: duckdb.DuckDBPyConnection) -> None:
    label = ", ".join(f"'{v}'" for v in LABEL_VALUES)
    con.execute(f"CREATE VIEW unit AS SELECT * FROM read_parquet('{UNITS.as_posix()}')")
    # Сигналы делятся по разметке суток: вода идёт в метку, проверка в признак
    # проверок (ADR 0012).
    for view, real in (("alarm", "true"), ("check_signal", "false")):
        con.execute(
            f"""
            CREATE TABLE {view} AS
            SELECT h.object_id, h.gallery, h.picket, h.hour, sum(h.n_moments) AS n_moments
            FROM read_parquet('{HOURLY.as_posix()}') h
            JOIN read_parquet('{PU_EVENTS.as_posix()}') e
              ON e.object_id = h.object_id AND e.gallery = h.gallery
             AND e.picket = h.picket AND e.day = h.hour::DATE
            WHERE h.value IN ({label}) AND e.real = {real}
            GROUP BY ALL
            """
        )
    con.execute(
        f"CREATE VIEW weather AS SELECT * FROM read_parquet('{WEATHER.as_posix()}')"
    )
    con.execute(
        f"CREATE VIEW pump AS SELECT * FROM read_parquet('{PUMP_HOURLY.as_posix()}')"
    )
    con.execute(
        f"""
        CREATE TABLE object_complex AS
        SELECT CAST("ид_объект" AS BIGINT) AS object_id, CAST("родитель" AS BIGINT) AS complex_id
        FROM read_csv('{OBJECTS.as_posix()}', header = true, all_varchar = true)
        """
    )
    con.execute(
        f"""
        CREATE TABLE unit_meta AS
        SELECT oid AS object_id, gal_key AS gallery, picket,
               count(*) FILTER (WHERE stype = 'Состояние насоса') AS n_pumps,
               count(*) FILTER (WHERE stype = 'Датчик затопления') AS n_flood_sensors
        FROM read_parquet('{CHANNELS.as_posix()}')
        WHERE stype IN ('Состояние насоса', 'Датчик затопления') AND picket IS NOT NULL
        GROUP BY ALL
        """
    )


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    attach_sources(con)

    t = time.time()
    blink = blink_threshold(con)
    print(f"порог мигания: больше {blink:.1f} смен за час (квантиль {BLINK_QUANTILE})")
    build_panel(con, blink)
    print(f"панель собрана за {time.time() - t:.0f} c")

    con.execute(f"COPY panel TO '{PANEL.as_posix()}' (FORMAT PARQUET)")
    row = con.execute(
        """
        SELECT count(*), sum(label), avg(label),
               count(*) FILTER (WHERE hour(hour) = 0),
               sum(label) FILTER (WHERE hour(hour) = 0),
               count(DISTINCT (object_id, gallery, picket)), min(hour), max(hour),
               avg(CAST(pump_blink_hours_24h > 0 AS INTEGER))
        FROM panel
        """
    ).fetchone()
    columns = [r[0] for r in con.execute("DESCRIBE panel").fetchall()]
    stats = {
        "blink_threshold_changes_per_hour": blink,
        "blink_quantile": BLINK_QUANTILE,
        "rows_hourly": int(row[0]),
        "positives_hourly": int(row[1]),
        "base_hourly": round(float(row[2]), 6),
        "rows_daily": int(row[3]),
        "positives_daily": int(row[4]),
        "units": int(row[5]),
        "hour_from": str(row[6]),
        "hour_to": str(row[7]),
        "share_rows_with_blink_24h": round(float(row[8]), 6),
        "features": [
            c
            for c in columns
            if c not in {"object_id", "gallery", "picket", "hour", "label", "naive"}
        ],
    }
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in stats.items() if k != "features"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"признаков: {len(stats['features'])}")


if __name__ == "__main__":
    main()
