"""Меряет метку направления «несанкционированный доступ» на любой витрине часов
с тревогой.

Метка: единица имеет тревогу доступа в окне (t, t + 24 ч]. Определение и разбор
чисел лежат в `backend/docs/adr/0001-access-target.md`.

Модуль считает три числа:

- база — доля положительных клеток;
- наивная планка — точность правила «событие повторится», то есть
  «тревога была в прошлом окне такой же длины»;
- полнота того же правила.

Наивная планка задаёт нижнюю границу. Модель обязана быть лучше неё.

Функции берут имя представления с часами тревоги. Так две версии метки
меряются одним кодом, и разница в числах идёт только от витрины.
"""
import pathlib

import duckdb

KEY = "object_id, gallery, picket"


def attach(con: duckdb.DuckDBPyConnection, path: pathlib.Path, view: str) -> None:
    """Подключает parquet как представление с именем `view`."""
    con.execute(
        f"CREATE OR REPLACE VIEW {view} AS "
        f"SELECT * FROM read_parquet('{path.as_posix()}')"
    )


def hourly_stats(
    con: duckdb.DuckDBPyConnection,
    horizon: int,
    alarm: str = "alarm",
    unit: str = "unit",
) -> dict:
    """Считает базу и наивную планку на часовой сетке с окном `horizon` часов.

    Полная панель «единица и час» занимает десятки миллионов строк, поэтому
    функция её не строит. Множество положительных часов равно объединению окон
    длиной `horizon`, которые кончаются перед каждым часом с тревогой. Такое
    множество считается напрямую и даёт тот же ответ.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {alarm}_positive AS
        SELECT DISTINCT {KEY}, hour - (s.i * INTERVAL 1 HOUR) AS hour
        FROM {alarm}, generate_series(1, {horizon}) s(i)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {alarm}_recent AS
        SELECT DISTINCT {KEY}, hour + (s.i * INTERVAL 1 HOUR) AS hour
        FROM {alarm}, generate_series(0, {horizon - 1}) s(i)
        """
    )
    for name in (f"{alarm}_positive", f"{alarm}_recent"):
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE {name}_alive AS
            SELECT p.* FROM {name} p
            JOIN {unit} u USING ({KEY})
            WHERE p.hour BETWEEN u.hour_from AND u.hour_to
            """
        )
    row = con.execute(
        f"""
        SELECT
            (SELECT sum(date_diff('hour', hour_from, hour_to) + 1) FROM {unit}),
            (SELECT count(*) FROM {alarm}_positive_alive),
            (SELECT count(*) FROM {alarm}_recent_alive),
            (SELECT count(*) FROM (
                SELECT * FROM {alarm}_positive_alive
                INTERSECT SELECT * FROM {alarm}_recent_alive))
        """
    ).fetchone()
    total, positive, predicted, hit = row
    return {
        "grid": f"единица и час, окно {horizon} ч",
        "cells": int(total),
        "positive": int(positive),
        "base_pct": round(100.0 * positive / total, 3),
        "naive_precision_pct": round(100.0 * hit / predicted, 3),
        "naive_recall_pct": round(100.0 * hit / positive, 3),
    }


def daily_stats(
    con: duckdb.DuckDBPyConnection,
    alarm: str = "alarm",
    unit: str = "unit",
) -> dict:
    """Считает те же числа на суточной сетке.

    Сетка нужна ради сверки с `INSIGHTS.md` §2.1: там замер сделан
    по дням. Правило наивной планки на ней читается как «событие повторится
    завтра».
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {alarm}_day_event AS
        SELECT DISTINCT {KEY}, hour::DATE AS day FROM {alarm}
        """
    )
    row = con.execute(
        f"""
        SELECT
            (SELECT sum(date_diff('day', hour_from::DATE, hour_to::DATE) + 1)
             FROM {unit}),
            (SELECT count(*) FROM {alarm}_day_event),
            (SELECT count(*) FROM {alarm}_day_event e
             WHERE exists(SELECT 1 FROM {alarm}_day_event n
                          WHERE n.object_id = e.object_id AND n.gallery = e.gallery
                            AND n.picket = e.picket AND n.day = e.day + 1))
        """
    ).fetchone()
    total, positive, repeated = row
    return {
        "grid": "единица и сутки",
        "cells": int(total),
        "positive": int(positive),
        "base_pct": round(100.0 * positive / total, 3),
        "naive_precision_pct": round(100.0 * repeated / positive, 3),
    }


def line(stats: dict) -> str:
    """Собирает строку вывода по одному замеру."""
    text = f"{stats['grid']}: клеток {stats['cells']}, база {stats['base_pct']} %"
    text += f", наивная точность {stats['naive_precision_pct']} %"
    if "naive_recall_pct" in stats:
        text += f", наивная полнота {stats['naive_recall_pct']} %"
    return text
