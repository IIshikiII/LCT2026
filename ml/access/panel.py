"""Суточная панель: строка «участок и сутки», метка и кандидаты в признаки.
Участок это отрезок хода вокруг узла входа, его строит `data.py`.

Метка: на участке было событие в прогнозируемые сутки T. Признаки считаются на
00:00 суток T − `LEAD_DAYS`. Сейчас `LEAD_DAYS` равен нулю: прогноз на
ближайшие сутки. Календарь и доля событий в эту дату берутся для суток T: их
знают заранее. Колонка `day` держит сутки T, колонка `as_of` точку расчёта.
Сутки, когда режим охраны объекта неизвестен, в панель не входят: метки там нет.
Точка расчёта стоит в 00:00 суток. Все окна признаков кончаются строго до неё.
Сетка плотная, поэтому окно `ROWS BETWEEN n PRECEDING AND 1 PRECEDING` равно
календарному окну в n суток.

Скрипт читает только результат `data.py` в `out/`. Результат:
`out/daily_panel.parquet`, `out/daily_panel_stats.json`.

Запуск из корня репозитория:

    .venv/bin/python ml/access/panel.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import duckdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import data

PANEL = data.OUT / "daily_panel.parquet"
STATS = data.OUT / "daily_panel_stats.json"

KEY = "object_id, gallery, section"

# Заблаговременность прогноза в сутках. Ноль значит прогноз на ближайшие сутки:
# на 00:00 суток T для суток T. Бэкенд пересчитывает прогноз раз в 15 минут, и
# карточка несёт время создания. Значение 1 даёт прогноз за 24 часа до начала
# суток. Замер обоих вариантов лежит в ADR 0001, правки от 25 и 26 сентября.
LEAD_DAYS = 0

# Окна истории в сутках. 365 суток ловят годовую сезонность, о которой говорил
# заказчик, 90 — квартальную, остальные три описывают свежесть.
WINDOWS = (1, 7, 30, 90, 365)


def build_day_grid(con: duckdb.DuckDBPyConnection) -> None:
    """Плотная сетка «единица и сутки» на весь срок жизни каждой единицы."""
    con.execute(
        """
        CREATE OR REPLACE TABLE day_grid AS
        SELECT u.object_id, u.gallery, u.section, d.day::DATE AS day
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
                        THEN n_moments ELSE 0 END) AS n_night,
               max(hour) AS last_hour
        FROM alarm GROUP BY ALL
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE armed_day AS
        SELECT {KEY}, date_trunc('day', hour)::DATE AS day,
               sum(n_moments) AS n_moments,
               max(hour) AS last_hour
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


def _rolling(column: str, days: int, name: str) -> str:
    """Сумма по окну прошлых суток, текущие сутки не входят."""
    return (
        f"sum({column}) OVER (PARTITION BY object_id, gallery, section ORDER BY day "
        f"ROWS BETWEEN {days} PRECEDING AND 1 PRECEDING) AS {name}"
    )


def build_context(con: duckdb.DuckDBPyConnection) -> None:
    """Признаки вне самого участка: соседи по линии, режим охраны на 00:00 и
    вся сеть. Все они смотрят только в прошлые сутки или в прошлые годы."""
    # Соседи: участки с номером на единицу больше и меньше на той же линии.
    # Номера узлов идут по пикетам, поэтому соседний номер это соседний вход.
    con.execute(
        """
        CREATE OR REPLACE TABLE neighbor_rolled AS
        WITH nb AS (
            SELECT a.object_id, a.gallery, a.section, a.day,
                   sum(b.had_armed) AS ev, sum(b.m_all) AS m
            FROM dense a
            JOIN dense b
              ON b.object_id = a.object_id AND b.gallery = a.gallery
             AND b.day = a.day AND abs(b.section - a.section) = 1
            GROUP BY ALL
        ),
        g AS (
            SELECT d.object_id, d.gallery, d.section, d.day,
                   coalesce(nb.ev, 0) AS ev, coalesce(nb.m, 0) AS m
            FROM dense d LEFT JOIN nb USING (object_id, gallery, section, day)
        )
        SELECT object_id, gallery, section, day,
            sum(ev) OVER w1 AS nb_events_1d,
            sum(ev) OVER w7 AS nb_events_7d,
            sum(m) OVER w7 AS nb_alarms_7d
        FROM g
        WINDOW
            w1 AS (PARTITION BY object_id, gallery, section ORDER BY day
                   ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING),
            w7 AS (PARTITION BY object_id, gallery, section ORDER BY day
                   ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
        """
    )
    # Режим охраны на 00:00. Смена режима это граница окна «Снято с охраны».
    con.execute(
        """
        CREATE OR REPLACE TABLE guard_day AS
        WITH od AS (SELECT DISTINCT object_id, day FROM dense),
        change AS (
            SELECT object_id, t_from AS ts FROM disarm_window
            UNION SELECT object_id, t_to FROM disarm_window WHERE NOT open_end
        ),
        covered AS (SELECT DISTINCT object_id FROM disarm_window)
        SELECT od.object_id, od.day,
            CASE WHEN od.object_id IN (SELECT object_id FROM covered) THEN
                CAST(EXISTS (SELECT 1 FROM disarm_window w
                             WHERE w.object_id = od.object_id
                               AND w.t_from <= od.day AND w.t_to > od.day) AS INTEGER)
            END AS is_disarmed_at_0,
            date_diff('hour', c.ts, od.day::TIMESTAMP) AS hours_since_guard_change
        FROM od
        ASOF LEFT JOIN change c ON c.object_id = od.object_id AND c.ts < od.day
        """
    )
    # Вся сеть: доля участков с событием за прошлые сутки и неделю. Доля
    # событий в эту же дату плюс-минус 3 суток в прошлые годы заменяет
    # признаки на каждый праздник и особую дату.
    con.execute(
        """
        CREATE OR REPLACE TABLE net_day_rolled AS
        WITH nd AS (
            SELECT day, sum(had_armed) AS ev, count(*) AS n FROM dense GROUP BY day
        ),
        rolled AS (
            SELECT day,
                sum(ev) OVER (ORDER BY day ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING)
                  / sum(n) OVER (ORDER BY day ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING)
                  AS net_event_share_1d,
                sum(ev) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
                  / sum(n) OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
                  AS net_event_share_7d
            FROM nd
        ),
        dated AS (
            SELECT a.day, sum(b.ev) / sum(b.n) AS date_event_share_prev_years
            FROM nd a JOIN nd b
              ON year(b.day) < year(a.day)
             AND least(abs(dayofyear(b.day) - dayofyear(a.day)),
                       365 - abs(dayofyear(b.day) - dayofyear(a.day))) <= 3
            GROUP BY a.day
        )
        SELECT r.*, d.date_event_share_prev_years
        FROM rolled r LEFT JOIN dated d USING (day)
        """
    )


def build_panel(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE dense AS
        SELECT
            g.object_id, g.gallery, g.section, g.day,
            coalesce(a.n_moments, 0) AS m_all,
            coalesce(a.n_night, 0) AS m_night,
            u.n_channels AS ch,
            CAST(a.n_moments IS NOT NULL AS INTEGER) AS had_alarm,
            coalesce(r.n_moments, 0) AS m_armed,
            CAST(r.n_moments IS NOT NULL AS INTEGER) AS had_armed,
            coalesce(w.disarm_hours, 0) AS disarm_hours,
            a.last_hour AS last_hour_all,
            r.last_hour AS last_hour_armed,
            coalesce(f.n_fault, 0) AS n_fault
        FROM day_grid g
        JOIN unit u USING ({KEY})
        LEFT JOIN alarm_day a USING ({KEY}, day)
        LEFT JOIN armed_day r USING ({KEY}, day)
        LEFT JOIN disarm_day w ON w.object_id = g.object_id AND w.day = g.day
        LEFT JOIN fault_day f USING ({KEY}, day)
        """
    )

    rolls = [
        *[_rolling("m_all", d, f"n_alarms_{d}d") for d in WINDOWS],
        *[_rolling("m_armed", d, f"n_armed_{d}d") for d in (1, 7, 30, 90)],
        *[_rolling("had_alarm", d, f"alarm_days_{d}d") for d in (7, 30, 90, 365)],
        *[_rolling("had_armed", d, f"armed_days_{d}d") for d in (7, 30, 90)],
        *[_rolling("m_night", d, f"night_moments_{d}d") for d in (30, 365)],
        *[_rolling("disarm_hours", d, f"disarm_hours_{d}d") for d in (1, 7, 30)],
        *[_rolling("n_fault", d, f"faults_{d}d") for d in (1, 7, 30)],
    ]

    con.execute(
        f"""
        CREATE OR REPLACE TABLE rolled AS
        SELECT
            object_id, gallery, section, day,
            lead(had_armed, {LEAD_DAYS}) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
            ) AS label,
            ch AS n_channels_unit,
            {", ".join(rolls)},
            max(CASE WHEN had_alarm = 1 THEN day END) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS last_alarm_day,
            max(CASE WHEN had_armed = 1 THEN day END) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS last_armed_day,
            max(last_hour_all) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS last_alarm_hour,
            max(last_hour_armed) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS last_armed_hour,
            min(day) OVER (PARTITION BY object_id, gallery, section) AS first_day,
            -- Та же единица ровно неделю назад: сезонность недели.
            lag(had_armed, 7) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
            ) AS armed_same_weekday_1w,
            -- Та же единица год назад, плюс-минус окно не берём: признак
            -- отвечает на вопрос «было ли в этот день в прошлом году».
            lag(had_armed, 364) OVER (
                PARTITION BY object_id, gallery, section ORDER BY day
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

    build_context(con)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE panel_daily AS
        SELECT
            r.object_id, r.gallery, r.section,
            CAST(r.day + {LEAD_DAYS} AS DATE) AS day, r.day AS as_of, r.label,

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
                  AND c.section = r.section
                  AND c.completed_ts >= r.day - INTERVAL 1 DAY
                  AND c.completed_ts < r.day
            ) AS INTEGER) AS has_access_chain_1d,

            -- свежесть в часах на 00:00
            coalesce(date_diff('hour', r.last_alarm_hour, r.day::TIMESTAMP),
                     24 * (date_diff('day', r.first_day, r.day) + 1)) AS hours_since_last_alarm,
            coalesce(date_diff('hour', r.last_armed_hour, r.day::TIMESTAMP),
                     24 * (date_diff('day', r.first_day, r.day) + 1)) AS hours_since_last_armed,

            -- отказы датчиков участка
            coalesce(r.faults_1d, 0) AS faults_1d,
            coalesce(r.faults_7d, 0) AS faults_7d,
            coalesce(r.faults_30d, 0) AS faults_30d,

            -- соседние участки той же линии
            coalesce(nb.nb_events_1d, 0) AS nb_events_1d,
            coalesce(nb.nb_events_7d, 0) AS nb_events_7d,
            coalesce(nb.nb_alarms_7d, 0) AS nb_alarms_7d,

            -- режим охраны объекта на 00:00; NULL, если канала режима нет
            gd.is_disarmed_at_0,
            gd.hours_since_guard_change,

            -- вся сеть
            nt.net_event_share_1d,
            nt.net_event_share_7d,
            nd.date_event_share_prev_years,

            -- паспорт участка
            si.is_branch, si.no_entry, si.n_pickets, si.n_contact, si.n_motion,
            si.n_emergency_exit, si.n_hatch, si.n_vent_shaft,

            -- устройство места
            r.n_channels_unit AS n_channels,
            date_diff('day', r.first_day, r.day) AS unit_age_days,

            -- календарь
            k.is_holiday, k.is_weekend, k.is_day_off, k.day_off_chain,
            k.is_long_weekend, k.is_school_vacation,
            k.day_of_week, k.month, k.quarter, k.season,
            k.day_of_month, k.week_of_year, k.day_of_year,

            -- наивная планка: событие было в последние известные сутки
            CAST(coalesce(r.n_armed_1d, 0) > 0 AS INTEGER) AS naive
        FROM rolled r
        LEFT JOIN object_rolled o ON o.object_id = r.object_id AND o.day = r.day
        LEFT JOIN neighbor_rolled nb
          ON nb.object_id = r.object_id AND nb.gallery = r.gallery
         AND nb.section = r.section AND nb.day = r.day
        LEFT JOIN guard_day gd ON gd.object_id = r.object_id AND gd.day = r.day
        LEFT JOIN net_day_rolled nt ON nt.day = r.day
        LEFT JOIN net_day_rolled nd ON nd.day = r.day + {LEAD_DAYS}
        LEFT JOIN section_info si
          ON si.object_id = r.object_id AND si.gallery = r.gallery AND si.section = r.section
        JOIN calendar k ON k.day = r.day + {LEAD_DAYS}
        WHERE r.label IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM guard_unknown u
            WHERE u.object_id = r.object_id
              AND u.t_from < r.day + INTERVAL {LEAD_DAYS + 1} DAY
              AND u.t_to > r.day + INTERVAL {LEAD_DAYS} DAY
        )
        ORDER BY r.object_id, r.gallery, r.section, r.day
        """
    )


def attach(con: duckdb.DuckDBPyConnection) -> None:
    """Подключает результат `data.py` как представления."""
    for view, path in (
        ("alarm", data.HOURLY),
        ("armed", data.ARMED),
        ("unit", data.UNITS),
        ("disarm_window", data.DISARM),
        ("fault_day", data.FAULTS),
        ("section_info", data.SECTION_INFO),
        ("guard_unknown", data.GUARD_UNKNOWN),
        ("access_chain", data.CHAIN),
        ("calendar", data.CALENDAR),
    ):
        con.execute(
            f"CREATE OR REPLACE VIEW {view} AS "
            f"SELECT * FROM read_parquet('{path.as_posix()}')"
        )


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='6GB'")
    attach(con)

    t = time.time()
    build_day_grid(con)
    build_daily_counts(con)
    build_panel(con)
    print(f"панель собрана за {time.time() - t:.0f} c")

    row = con.execute(
        """
        SELECT count(*) AS rows, sum(label) AS positives,
               avg(label) AS base, count(DISTINCT (object_id, gallery, section)) AS units,
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
    }
    con.execute(f"COPY panel_daily TO '{PANEL.as_posix()}' (FORMAT PARQUET)")
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"панель в {PANEL}")


if __name__ == "__main__":
    main()
