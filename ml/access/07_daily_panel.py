"""Суточная панель направления «несанкционированный доступ».

Диспетчер по часовому прогнозу не выезжает: наряд живёт сутками. Сетка
приведена к тому шагу, которым пользуются на самом деле.

**База события от этого не выросла, и это надо знать.** Ожидание было, что
укрупнение сетки поднимет базу в двадцать раз. Замер показал 0,557 % против
0,558 % на часах, то есть ровно столько же. Причина в том, что метка и так
смотрела на сутки вперёд: доля часов, попадающих в сутки перед событием, равна
доле суток с событием. Выигрыш пришёл не отсюда.

**Выигрыш пришёл от полной сетки и от новых признаков.** Часовая панель
оставляла 2 % отрицательных клеток и несла веса. Суточная сетка это 2,6 млн
клеток вместо 62,6 млн, она помещается в память целиком, и веса уходят вместе с
целым классом ошибок при сравнении с наивной планкой. На этой сетке дёшево
считаются окна в год и календарные признаки, которых на часовой сетке не было.

**Сетка.** Строка это «единица и сутки». Единица — тройка «объект, галерея,
пикет». Точка расчёта стоит в 00:00 суток, все окна признаков строго до неё.

**Метка.** Единица получает тревогу доступа вне окна снятия охраны в течение
этих суток. Горизонт ровно 24 часа, требование ТЗ §6 держится.

**Признаки считаются оконными функциями, а не подзапросами.** Сетка плотная:
у каждой единицы есть строка на каждые сутки жизни. Поэтому `ROWS BETWEEN
n PRECEDING AND 1 PRECEDING` совпадает с календарным окном день в день, и
тридцать признаков считаются одним проходом вместо тридцати подзапросов.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/access/07_daily_panel.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import duckdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import calendar_ru
import features

OUT = pathlib.Path(__file__).resolve().parent / "out"
PANEL = OUT / "daily_panel.parquet"
CHAIN = OUT / "access_chain.parquet"
STATS = OUT / "daily_panel_stats.json"

KEY = "object_id, gallery, picket"

# Окна истории в сутках. 365 суток ловят годовую сезонность, о которой говорил
# заказчик, 90 — квартальную, остальные три описывают свежесть.
WINDOWS = (1, 7, 30, 90, 365)


def build_day_grid(con: duckdb.DuckDBPyConnection) -> None:
    """Плотная сетка «единица и сутки» на весь срок жизни каждой единицы."""
    con.execute(
        """
        CREATE OR REPLACE TABLE day_grid AS
        SELECT u.object_id, u.gallery, u.picket, d.day::DATE AS day
        FROM unit u,
             LATERAL generate_series(
                 date_trunc('day', u.hour_from),
                 date_trunc('day', u.hour_to),
                 INTERVAL 1 DAY
             ) AS d(day)
        """
    )


def build_daily_counts(con: duckdb.DuckDBPyConnection) -> None:
    """Суточные своды тревог: все и только вне окна снятия охраны."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE alarm_day AS
        SELECT {KEY}, date_trunc('day', hour)::DATE AS day,
               sum(n_moments) AS n_moments,
               max(n_channels) AS n_channels,
               sum(CASE WHEN hour(hour) >= 22 OR hour(hour) < 6
                        THEN n_moments ELSE 0 END) AS n_night
        FROM alarm GROUP BY ALL
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE armed_day AS
        SELECT {KEY}, date_trunc('day', hour)::DATE AS day,
               sum(n_moments) AS n_moments
        FROM armed GROUP BY ALL
        """
    )
    # Часы снятия охраны по объекту и суткам. Окно может лежать на нескольких
    # сутках, поэтому оно режется по границам дня.
    con.execute(
        """
        CREATE OR REPLACE TABLE disarm_day AS
        WITH cut AS (
            SELECT w.object_id, d.day::DATE AS day,
                   greatest(w.t_from, d.day) AS a,
                   least(w.t_to, d.day + INTERVAL 1 DAY) AS b
            FROM disarm_window w,
                 LATERAL generate_series(
                     date_trunc('day', w.t_from),
                     date_trunc('day', w.t_to),
                     INTERVAL 1 DAY
                 ) AS d(day)
            WHERE w.t_to > w.t_from
        )
        SELECT object_id, day,
               sum(date_diff('minute', a, b)) / 60.0 AS disarm_hours
        FROM cut WHERE b > a GROUP BY ALL
        """
    )


def build_chain(con: duckdb.DuckDBPyConnection, near: int, minutes: int) -> int:
    """Цепочки «дверь, объёмный датчик, движение». Кэшируются в parquet.

    Разбор идёт по выгрузке на 1,8 ГБ и стоит минуты, а подбор окна требует
    нескольких прогонов. Поэтому результат кладётся рядом и переиспользуется,
    пока параметры не изменились.
    """
    meta = OUT / f"access_chain_{near}p_{minutes}m.parquet"
    if not meta.exists():
        features.NEAR_PICKETS = near
        features.SEQUENCE_WINDOW_MINUTES = minutes
        features.build_sequence_chain(con)
        con.execute(f"COPY access_chain TO '{meta.as_posix()}' (FORMAT PARQUET)")
    con.execute(
        f"CREATE OR REPLACE VIEW access_chain AS "
        f"SELECT * FROM read_parquet('{meta.as_posix()}')"
    )
    return int(con.execute("SELECT count(*) FROM access_chain").fetchone()[0])


def _rolling(column: str, days: int, name: str) -> str:
    """Сумма по окну прошлых суток, текущие сутки не входят."""
    return (
        f"sum({column}) OVER (PARTITION BY object_id, gallery, picket ORDER BY day "
        f"ROWS BETWEEN {days} PRECEDING AND 1 PRECEDING) AS {name}"
    )


def build_panel(con: duckdb.DuckDBPyConnection, near: int, minutes: int) -> None:
    calendar_view = calendar_ru.build_calendar(con)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE dense AS
        SELECT
            g.object_id, g.gallery, g.picket, g.day,
            coalesce(a.n_moments, 0) AS m_all,
            coalesce(a.n_night, 0) AS m_night,
            u.n_channels AS ch,
            CAST(a.n_moments IS NOT NULL AS INTEGER) AS had_alarm,
            coalesce(r.n_moments, 0) AS m_armed,
            CAST(r.n_moments IS NOT NULL AS INTEGER) AS had_armed,
            coalesce(w.disarm_hours, 0) AS disarm_hours
        FROM day_grid g
        JOIN unit u USING ({KEY})
        LEFT JOIN alarm_day a USING ({KEY}, day)
        LEFT JOIN armed_day r USING ({KEY}, day)
        LEFT JOIN disarm_day w ON w.object_id = g.object_id AND w.day = g.day
        """
    )

    rolls = [
        *[_rolling("m_all", d, f"n_alarms_{d}d") for d in WINDOWS],
        *[_rolling("m_armed", d, f"n_armed_{d}d") for d in (1, 7, 30, 90)],
        *[_rolling("had_alarm", d, f"alarm_days_{d}d") for d in (7, 30, 90, 365)],
        *[_rolling("had_armed", d, f"armed_days_{d}d") for d in (7, 30, 90)],
        *[_rolling("m_night", d, f"night_moments_{d}d") for d in (30, 365)],
        *[_rolling("disarm_hours", d, f"disarm_hours_{d}d") for d in (1, 7, 30)],
    ]

    con.execute(
        f"""
        CREATE OR REPLACE TABLE rolled AS
        SELECT
            object_id, gallery, picket, day,
            had_armed AS label,
            ch AS n_channels_unit,
            {", ".join(rolls)},
            max(CASE WHEN had_alarm = 1 THEN day END) OVER (
                PARTITION BY object_id, gallery, picket ORDER BY day
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS last_alarm_day,
            max(CASE WHEN had_armed = 1 THEN day END) OVER (
                PARTITION BY object_id, gallery, picket ORDER BY day
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS last_armed_day,
            min(day) OVER (PARTITION BY object_id, gallery, picket) AS first_day,
            -- Та же единица ровно неделю назад: сезонность недели.
            lag(had_armed, 7) OVER (
                PARTITION BY object_id, gallery, picket ORDER BY day
            ) AS armed_same_weekday_1w,
            -- Та же единица год назад, плюс-минус окно не берём: признак
            -- отвечает на вопрос «было ли в этот день в прошлом году».
            lag(had_armed, 364) OVER (
                PARTITION BY object_id, gallery, picket ORDER BY day
            ) AS armed_same_day_1y
        FROM dense
        """
    )

    # Соседство по объекту: сколько тревог дали остальные пикеты того же
    # объекта. Признак отвечает на вопрос «шумит ли объект целиком».
    con.execute(
        """
        CREATE OR REPLACE TABLE object_day AS
        SELECT object_id, day, sum(m_all) AS m_obj, sum(had_alarm) AS units_alarmed
        FROM dense GROUP BY ALL
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE object_rolled AS
        SELECT object_id, day,
            sum(m_obj) OVER (PARTITION BY object_id ORDER BY day
                ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING) AS obj_alarms_1d,
            sum(m_obj) OVER (PARTITION BY object_id ORDER BY day
                ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS obj_alarms_7d,
            sum(units_alarmed) OVER (PARTITION BY object_id ORDER BY day
                ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS obj_units_alarmed_7d
        FROM object_day
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE panel_daily AS
        SELECT
            r.object_id, r.gallery, r.picket, r.day, r.label,

            -- частота тревог
            coalesce(r.n_alarms_1d, 0) AS n_alarms_1d,
            coalesce(r.n_alarms_7d, 0) AS n_alarms_7d,
            coalesce(r.n_alarms_30d, 0) AS n_alarms_30d,
            coalesce(r.n_alarms_90d, 0) AS n_alarms_90d,
            coalesce(r.n_alarms_365d, 0) AS n_alarms_365d,
            coalesce(r.n_armed_1d, 0) AS n_armed_1d,
            coalesce(r.n_armed_7d, 0) AS n_armed_7d,
            coalesce(r.n_armed_30d, 0) AS n_armed_30d,
            coalesce(r.n_armed_90d, 0) AS n_armed_90d,

            -- доля суток с тревогой
            coalesce(r.alarm_days_7d, 0) / 7.0 AS alarm_day_share_7d,
            coalesce(r.alarm_days_30d, 0) / 30.0 AS alarm_day_share_30d,
            coalesce(r.alarm_days_90d, 0) / 90.0 AS alarm_day_share_90d,
            coalesce(r.alarm_days_365d, 0) / 365.0 AS alarm_day_share_365d,
            coalesce(r.armed_days_30d, 0) / 30.0 AS armed_day_share_30d,

            -- тренд: свежая частота против фоновой
            (coalesce(r.alarm_days_7d, 0) / 7.0)
                / greatest(coalesce(r.alarm_days_90d, 0) / 90.0, 0.001) AS trend_7_90,

            -- свежесть
            coalesce(date_diff('day', r.last_alarm_day, r.day),
                     date_diff('day', r.first_day, r.day) + 1) AS days_since_last_alarm,
            coalesce(date_diff('day', r.last_armed_day, r.day),
                     date_diff('day', r.first_day, r.day) + 1) AS days_since_last_armed,

            -- ночная доля
            coalesce(r.night_moments_30d, 0)
                / greatest(coalesce(r.n_alarms_30d, 0), 1) AS night_share_30d,
            coalesce(r.night_moments_365d, 0)
                / greatest(coalesce(r.n_alarms_365d, 0), 1) AS night_share_365d,

            -- снятие с охраны
            coalesce(r.disarm_hours_1d, 0) AS disarm_hours_1d,
            coalesce(r.disarm_hours_7d, 0) AS disarm_hours_7d,
            coalesce(r.disarm_hours_30d, 0) / (30 * 24.0) AS disarm_share_30d,

            -- повторяемость
            coalesce(r.armed_same_weekday_1w, 0) AS armed_same_weekday_1w,
            coalesce(r.armed_same_day_1y, 0) AS armed_same_day_1y,

            -- соседи по объекту
            coalesce(o.obj_alarms_1d, 0) AS obj_alarms_1d,
            coalesce(o.obj_alarms_7d, 0) AS obj_alarms_7d,
            coalesce(o.obj_units_alarmed_7d, 0) AS obj_units_alarmed_7d,

            -- цепочка доступа за прошлые сутки
            CAST(EXISTS (
                SELECT 1 FROM access_chain c
                WHERE c.object_id = r.object_id AND c.gallery = r.gallery
                  AND abs(c.picket - r.picket) <= {near}
                  AND c.completed_ts >= r.day - INTERVAL 1 DAY
                  AND c.completed_ts < r.day
            ) AS INTEGER) AS has_access_chain_1d,

            -- устройство места
            r.n_channels_unit AS n_channels,
            date_diff('day', r.first_day, r.day) AS unit_age_days,

            -- календарь
            k.is_holiday, k.is_weekend, k.is_day_off, k.day_off_chain,
            k.is_long_weekend, k.is_school_vacation,
            k.day_of_week, k.month, k.quarter, k.season,
            k.day_of_month, k.week_of_year, k.day_of_year,

            -- наивная планка: тревога вне охраны была вчера
            CAST(coalesce(r.n_armed_1d, 0) > 0 AS INTEGER) AS naive
        FROM rolled r
        LEFT JOIN object_rolled o ON o.object_id = r.object_id AND o.day = r.day
        JOIN {calendar_view} k ON k.day = r.day
        ORDER BY r.object_id, r.gallery, r.picket, r.day
        """
    )


def main() -> None:
    near = int(sys.argv[1]) if len(sys.argv) > 1 else features.NEAR_PICKETS
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else features.SEQUENCE_WINDOW_MINUTES

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    features.attach_sources(con)

    t = time.time()
    n_chains = build_chain(con, near, minutes)
    print(f"цепочек доступа ({near} пикетов, {minutes} мин): {n_chains}, {time.time() - t:.0f} c")

    t = time.time()
    build_day_grid(con)
    build_daily_counts(con)
    build_panel(con, near, minutes)
    print(f"панель собрана за {time.time() - t:.0f} c")

    row = con.execute(
        """
        SELECT count(*) AS rows, sum(label) AS positives,
               avg(label) AS base, count(DISTINCT (object_id, gallery, picket)) AS units,
               min(day) AS day_from, max(day) AS day_to, avg(naive) AS naive_share
        FROM panel_daily
        """
    ).fetchone()
    stats = {
        "rows": int(row[0]),
        "positives": int(row[1]),
        "base": round(float(row[2]), 6),
        "units": int(row[3]),
        "day_from": str(row[4]),
        "day_to": str(row[5]),
        "naive_share": round(float(row[6]), 6),
        "near_pickets": near,
        "sequence_window_minutes": minutes,
    }
    con.execute(f"COPY panel_daily TO '{PANEL.as_posix()}' (FORMAT PARQUET)")
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"панель в {PANEL}")


if __name__ == "__main__":
    main()
