"""Меряет метку направления «несанкционированный доступ» на витрине из
`01_dataset.py`.

Метка: единица имеет тревогу доступа в окне (t, t + 24 ч]. Определение и разбор
чисел лежат в `backend/docs/adr/0001-access-target.md`.

Скрипт считает три числа на трёх сетках и пишет их в `out/target_stats.json`:

- база — доля положительных клеток;
- наивная планка — точность правила «событие повторится», то есть
  «тревога была в прошлом окне такой же длины»;
- полнота того же правила.

Наивная планка задаёт нижнюю границу. Модель обязана быть лучше неё.
"""
import json
import pathlib
import duckdb

OUT = pathlib.Path(__file__).resolve().parent / "out"
HOURLY = OUT / "access_hourly.parquet"
UNITS = OUT / "access_units.parquet"
STATS = OUT / "target_stats.json"

HORIZON_HOURS = 24

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='6GB'")
con.execute(f"CREATE VIEW alarm AS SELECT * FROM read_parquet('{HOURLY.as_posix()}')")
con.execute(f"CREATE VIEW unit AS SELECT * FROM read_parquet('{UNITS.as_posix()}')")

KEY = "object_id, gallery, picket"


def hourly_stats(horizon: int) -> dict:
    """Считает базу и наивную планку на часовой сетке с окном `horizon` часов.

    Полная панель «единица и час» занимает десятки миллионов строк, поэтому
    скрипт её не строит. Множество положительных часов равно объединению окон
    длиной `horizon`, которые кончаются перед каждым часом с тревогой. Такое
    множество считается напрямую и даёт тот же ответ.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE positive AS
        SELECT DISTINCT {KEY}, hour - (s.i * INTERVAL 1 HOUR) AS hour
        FROM alarm, generate_series(1, {horizon}) s(i)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE recent AS
        SELECT DISTINCT {KEY}, hour + (s.i * INTERVAL 1 HOUR) AS hour
        FROM alarm, generate_series(0, {horizon - 1}) s(i)
        """
    )
    for name in ("positive", "recent"):
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE {name}_alive AS
            SELECT p.* FROM {name} p
            JOIN unit u USING ({KEY})
            WHERE p.hour BETWEEN u.hour_from AND u.hour_to
            """
        )
    row = con.execute(
        f"""
        SELECT
            (SELECT sum(date_diff('hour', hour_from, hour_to) + 1) FROM unit),
            (SELECT count(*) FROM positive_alive),
            (SELECT count(*) FROM recent_alive),
            (SELECT count(*) FROM (
                SELECT * FROM positive_alive INTERSECT SELECT * FROM recent_alive))
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


def daily_stats() -> dict:
    """Считает те же числа на суточной сетке.

    Сетка нужна ради сверки с `hackathon-gap-analysis.md` §A4: там замер сделан
    по дням. Правило наивной планки на ней читается как «событие повторится
    завтра».
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE day_event AS
        SELECT DISTINCT {KEY}, hour::DATE AS day FROM alarm
        """
    )
    row = con.execute(
        f"""
        SELECT
            (SELECT sum(date_diff('day', hour_from::DATE, hour_to::DATE) + 1) FROM unit),
            (SELECT count(*) FROM day_event),
            (SELECT count(*) FROM day_event e
             WHERE exists(SELECT 1 FROM day_event n
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


result = {
    "horizon_hours": HORIZON_HOURS,
    "units": con.execute("SELECT count(*) FROM unit").fetchone()[0],
    "channels": con.execute("SELECT sum(n_channels) FROM unit").fetchone()[0],
    "alarm_hours": con.execute("SELECT count(*) FROM alarm").fetchone()[0],
    "alarm_moments": int(con.execute("SELECT sum(n_moments) FROM alarm").fetchone()[0]),
    "hourly": hourly_stats(HORIZON_HOURS),
    "daily": daily_stats(),
}

STATS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"единиц: {result['units']}, каналов: {result['channels']}")
print(f"часов с тревогой: {result['alarm_hours']}, моментов: {result['alarm_moments']}")
for key in ("hourly", "daily"):
    s = result[key]
    line = f"{s['grid']}: клеток {s['cells']}, база {s['base_pct']} %"
    line += f", наивная точность {s['naive_precision_pct']} %"
    if "naive_recall_pct" in s:
        line += f", наивная полнота {s['naive_recall_pct']} %"
    print(line)
print(f"замер записан в {STATS}")
