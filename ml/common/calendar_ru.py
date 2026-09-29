"""Календарь России, общий для признаков направлений в `ml/`.

Заказчик на QA-сессии назвал календарь прямо: длинные праздники и
особые даты меняют поведение нарушителя и подрядчика. Доклад СМВУ добавил
второй довод: участок снимают с охраны, когда туда законно спускаются
подрядчики, а подрядчик работает по рабочему календарю.

Таблица строится из правил, а не из внешней службы. Причина простая: сеть
отрезана от интернета на проде, а календарь на семь лет назад и один вперёд
помещается в сто строк кода.

**Чего таблица не знает.** Переносы выходных правительство назначает своим
постановлением на каждый год, и они не выводятся из правил. Здесь заложены
только постоянные нерабочие дни и новогодние каникулы. Отдельные перенесённые
дни (например рабочая суббота перед длинными выходными) считаются обычными.
Ошибка — единицы дней в году, и она одинакова во всех годах выборки.

Школьные каникулы взяты по типовому расписанию московских школ с четвертями.
Точные даты школа назначает сама, поэтому границы приблизительные.
"""

from __future__ import annotations

import datetime as dt

import duckdb

# Постоянные нерабочие дни: месяц и день. Указ о переносах сюда не входит.
FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),  # новогодние каникулы
    (2, 23),  # День защитника Отечества
    (3, 8),  # Международный женский день
    (5, 1),  # Праздник Весны и Труда
    (5, 9),  # День Победы
    (6, 12),  # День России
    (11, 4),  # День народного единства
)

# Школьные каникулы: месяц и день начала, месяц и день конца, включительно.
# Летние каникулы держат три месяца и меняют поведение сильнее остальных.
SCHOOL_VACATIONS: tuple[tuple[int, int, int, int], ...] = (
    (10, 26, 11, 3),  # осенние
    (12, 28, 1, 8),  # зимние, переходят через год
    (3, 23, 3, 31),  # весенние
    (6, 1, 8, 31),  # летние
)


def is_holiday(day: dt.date) -> bool:
    return (day.month, day.day) in FIXED_HOLIDAYS


def is_school_vacation(day: dt.date) -> bool:
    for m_from, d_from, m_to, d_to in SCHOOL_VACATIONS:
        start = (m_from, d_from)
        end = (m_to, d_to)
        now = (day.month, day.day)
        if start <= end:
            if start <= now <= end:
                return True
        # Отрезок через новый год: попадание либо после начала, либо до конца.
        elif now >= start or now <= end:
            return True
    return False


def season(day: dt.date) -> int:
    """Зима 0, весна 1, лето 2, осень 3. Декабрь идёт к зиме, а не к осени."""
    return ((day.month % 12) // 3)


def build_calendar(
    con: duckdb.DuckDBPyConnection,
    first: dt.date = dt.date(2018, 1, 1),
    last: dt.date = dt.date(2027, 12, 31),
    table: str = "calendar",
) -> str:
    """Кладёт календарь в таблицу `calendar` с одной строкой на дату.

    Отрезок шире выгрузки с обеих сторон: признаки «дней до праздника» и
    «дней после праздника» смотрят за край периода.
    """
    rows = []
    day = first
    while day <= last:
        rows.append(
            (
                day,
                int(is_holiday(day)),
                int(day.weekday() >= 5),
                int(is_school_vacation(day)),
                day.weekday(),
                day.month,
                (day.month - 1) // 3 + 1,
                season(day),
                day.day,
                day.isocalendar().week,
                day.timetuple().tm_yday,
            )
        )
        day += dt.timedelta(days=1)

    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} (
            day DATE PRIMARY KEY,
            is_holiday INTEGER,
            is_weekend INTEGER,
            is_school_vacation INTEGER,
            day_of_week INTEGER,
            month INTEGER,
            quarter INTEGER,
            season INTEGER,
            day_of_month INTEGER,
            week_of_year INTEGER,
            day_of_year INTEGER
        )
        """
    )
    con.executemany(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )

    # Производные признаки считаются оконными функциями поверх готовых дней.
    # Так «длина цепочки выходных» и «дней до праздника» выводятся один раз и
    # одинаково, а не переписываются в каждом запросе панели.
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {table}_full AS
        WITH base AS (
            SELECT *, CAST(is_holiday = 1 OR is_weekend = 1 AS INTEGER) AS is_day_off
            FROM {table}
        ),
        runs AS (
            SELECT *,
                sum(CASE WHEN is_day_off = 0 THEN 1 ELSE 0 END)
                    OVER (ORDER BY day) AS work_run
            FROM base
        ),
        chains AS (
            SELECT *,
                CASE WHEN is_day_off = 1
                     THEN count(*) OVER (PARTITION BY work_run, is_day_off)
                     ELSE 0 END AS day_off_chain
            FROM runs
        )
        SELECT
            day,
            is_holiday,
            is_weekend,
            is_school_vacation,
            is_day_off,
            day_off_chain,
            -- Длинные выходные: три подряд и больше. Заказчик назвал их сам.
            CAST(day_off_chain >= 3 AS INTEGER) AS is_long_weekend,
            day_of_week,
            month,
            quarter,
            season,
            day_of_month,
            week_of_year,
            day_of_year
        FROM chains
        """
    )
    return f"{table}_full"
