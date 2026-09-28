"""Меряет версии метки пожарного риска рядом. ADR 0014.

Сетка это «пикет и сутки» в границах жизни пикета. Событие суток это хотя бы
один сигнал метки в эти сутки.

- `raw`: любой сигнал.
- `off_window`: сигнал вне окна «рабочий день 8:00–16:00».
- `off_hours`: сигнал ночью (22:00–6:00) или в нерабочий день. Это
  размеченные примеры классификатора проверок.

Для каждой версии скрипт считает число событий, базу и наивную планку «событие
было на пикете вчера» по годам. Отложенный год (с 2025-07-01) даёт только
базу: планку на нём скрипт не печатает, чтобы выбор версии его не видел.

Ещё одно число: ритм недели. Это частота суток с событием в будни к частоте в
нерабочие дни на разрешённый час. Будни разрешают версии `off_window` 16 часов
из 24, нерабочий день все 24. Без графика работ ритм близок к 1.

Результат: `out/target_stats.json`.
"""

import json
import pathlib
import sys

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
HOURLY = OUT / "fire_hourly.parquet"
UNITS = OUT / "fire_units.parquet"
RESULT = OUT / "target_stats.json"
sys.path.insert(0, str(HERE.parents[0] / "common"))

import calendar_ru

TEST_START = "2025-07-01"
# Доля часов суток, в которые версия может засчитать сигнал.
ALLOWED_HOURS = {
    "raw": (24, 24),
    "off_window": (16, 24),
    "off_hours": (8, 24),
}

con = duckdb.connect()
cal = calendar_ru.build_calendar(con)

con.execute(
    f"""
    CREATE TABLE sig AS
    SELECT h.object_id, h.gallery, h.picket, h.hour::DATE AS day,
        max(1) AS raw,
        max((k.is_day_off = 1 OR hour(h.hour) < 8 OR hour(h.hour) >= 16)::INT) AS off_window,
        max((k.is_day_off = 1 OR hour(h.hour) < 6 OR hour(h.hour) >= 22)::INT) AS off_hours
    FROM read_parquet('{HOURLY.as_posix()}') h
    JOIN {cal} k ON k.day = h.hour::DATE
    GROUP BY 1, 2, 3, 4
    """
)
con.execute(
    f"""
    CREATE TABLE grid AS
    SELECT u.object_id, u.gallery, u.picket, g.day::DATE AS day, k.is_day_off
    FROM read_parquet('{UNITS.as_posix()}') u,
         generate_series(u.hour_from::DATE, u.hour_to::DATE, INTERVAL 1 DAY) g(day)
    JOIN {cal} k ON k.day = g.day::DATE
    """
)
con.execute(
    """
    CREATE TABLE panel AS
    SELECT g.*, coalesce(s.raw, 0) AS raw, coalesce(s.off_window, 0) AS off_window,
        coalesce(s.off_hours, 0) AS off_hours
    FROM grid g
    LEFT JOIN sig s USING (object_id, gallery, picket, day)
    """
)

stats: dict[str, object] = {}
for version, (work_hours, off_hours) in ALLOWED_HOURS.items():
    rows = con.execute(
        f"""
        WITH p AS (
            SELECT *, lag({version}) OVER w AS yesterday, lag(day) OVER w AS prev_day
            FROM panel
            WINDOW w AS (PARTITION BY object_id, gallery, picket ORDER BY day)
        ),
        q AS (
            SELECT *,
                CASE WHEN day < DATE '{TEST_START}' THEN year(day)::VARCHAR
                     ELSE 'holdout' END AS period,
                coalesce(yesterday = 1 AND prev_day = day - 1, false) AS alert
            FROM p
        )
        SELECT period, count(*) AS unit_days, sum({version}) AS events,
            sum(alert::INT) AS alerts, sum((alert AND {version} = 1)::INT) AS hits,
            sum({version}) FILTER (WHERE is_day_off = 0) AS ev_work,
            count(*) FILTER (WHERE is_day_off = 0) AS n_work,
            sum({version}) FILTER (WHERE is_day_off = 1) AS ev_off,
            count(*) FILTER (WHERE is_day_off = 1) AS n_off
        FROM q GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    per_year = {}
    train = {"unit_days": 0, "events": 0, "alerts": 0, "hits": 0}
    for period, n, ev, alerts, hits, ev_w, n_w, ev_o, n_o in rows:
        item: dict[str, object] = {"unit_days": n, "events": ev, "base": round(ev / n, 6)}
        if period != "holdout":
            item["naive_precision"] = round(hits / alerts, 4) if alerts else None
            item["naive_recall"] = round(hits / ev, 4) if ev else None
            for key, value in (("unit_days", n), ("events", ev), ("alerts", alerts), ("hits", hits)):
                train[key] += value
            rate_work = ev_w / n_w / work_hours
            rate_off = ev_o / n_o / off_hours if ev_o else None
            item["week_rhythm"] = round(rate_work / rate_off, 3) if rate_off else None
        per_year[period] = item
    stats[version] = {
        "train_events": train["events"],
        "train_base": round(train["events"] / train["unit_days"], 6),
        "train_naive_precision": round(train["hits"] / train["alerts"], 4),
        "train_naive_recall": round(train["hits"] / train["events"], 4),
        "by_period": per_year,
    }
    print(
        f"{version}: событий до {TEST_START} {train['events']}, "
        f"база {stats[version]['train_base']:.4%}, "
        f"планка P {stats[version]['train_naive_precision']:.3f} "
        f"R {stats[version]['train_naive_recall']:.3f}"
    )
    for period, item in per_year.items():
        print(f"  {period}: {item}")

units = con.execute(f"SELECT count(*) FROM read_parquet('{UNITS.as_posix()}')").fetchone()[0]
stats["units"] = units
RESULT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"единиц {units}, замер записан в {RESULT.name}")
