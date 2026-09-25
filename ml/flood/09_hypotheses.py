"""Проверяет гипотезы о том, какой сигнал затопления означает воду. ADR 0010.

Каждый критерий называет, какие часы с сигналом «Затоплен» насоса или «Не
замкнут» датчика затопления считать водой. Сутки единицы положительны, если в
них есть хотя бы один такой час. Для каждого критерия скрипт считает три
условия правила выбора и свидетельства для отчёта.

- Объём на обучающем отрезке и доля крупнейшего объекта.
- Ритм недели: частота засчитанных сигналов на разрешённый час в будни,
  делённая на ту же частоту в выходные и праздники. Вода даёт единицу.
- Погодный лифт: частота положительных суток в мокрые сутки, делённая на
  частоту в сухие.
- Для отчёта: работа насоса в эти сутки против обычной, доля суток, у которых
  сигнал продолжается накануне или назавтра, пик по часам суток.

Рабочее окно: будни, кроме праздников, с 8:00 до 16:00. Праздники берутся из
`ml/access/calendar_ru.py`.

Выход: `out/hypotheses.json`.
"""

import json
import pathlib
import sys

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "access"))

import calendar_ru

OUT = HERE / "out"
HOURLY = OUT / "flood_hourly.parquet"
UNITS = OUT / "flood_units.parquet"
PUMPS = OUT / "pump_hourly.parquet"
WEATHER = OUT / "weather_daily.parquet"
RESULT = OUT / "hypotheses.json"

TRAIN_END = "2025-07-01"
WORK_FROM, WORK_TO = 8, 16
WET_RAIN_MM = 10.0
WET_MELT_CM = 3.0

con = duckdb.connect()
con.execute("PRAGMA threads=8")
cal = calendar_ru.build_calendar(con)

# Часы с сигналом метки и свойства часа.
con.execute(
    f"""
    CREATE TABLE sig AS
    SELECT h.object_id, h.gallery, h.picket, h.hour, h.hour::DATE AS day,
           max(CASE WHEN h.value = 'Не замкнут' THEN 1 ELSE 0 END) AS from_sensor,
           max(CASE WHEN h.value = 'Затоплен' THEN 1 ELSE 0 END) AS from_pump,
           sum(h.n_moments) AS moments,
           k.is_day_off = 0 AND hour(h.hour) >= {WORK_FROM} AND hour(h.hour) < {WORK_TO}
               AS in_work,
           hour(h.hour) >= 22 OR hour(h.hour) < 6 AS at_night,
           k.is_day_off = 1 AS day_off
    FROM read_parquet('{HOURLY.as_posix()}') h
    JOIN {cal} k ON k.day = h.hour::DATE
    WHERE h.value IN ('Затоплен', 'Не замкнут')
    GROUP BY h.object_id, h.gallery, h.picket, h.hour, k.is_day_off
    """
)
# Сутки с сигналом и их свойства.
con.execute(
    """
    CREATE TABLE ep AS
    WITH d AS (
        SELECT object_id, gallery, picket, day,
               count(*) AS hrs, bool_and(in_work) AS work_only,
               max(from_sensor) AS any_sensor, max(from_pump) AS any_pump
        FROM sig GROUP BY ALL
    )
    SELECT d.*,
           exists(SELECT 1 FROM d n WHERE n.object_id = d.object_id AND n.gallery = d.gallery
                  AND n.picket = d.picket AND n.day IN (d.day - 1, d.day + 1)) AS adjacent,
           exists(SELECT 1 FROM d n WHERE n.object_id = d.object_id AND n.day = d.day
                  AND (n.gallery <> d.gallery OR n.picket <> d.picket)) AS neighbour
    FROM d
    """
)
# Работа насоса единицы по суткам и её обычный уровень.
con.execute(
    f"""
    CREATE TABLE pump_day AS
    SELECT object_id, gallery, picket, hour::DATE AS day, sum(on_minutes) AS on_min
    FROM read_parquet('{PUMPS.as_posix()}') WHERE picket IS NOT NULL GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE pump_norm AS
    SELECT object_id, gallery, picket, avg(on_min) AS mean_on
    FROM pump_day GROUP BY ALL
    """
)
con.execute(
    """
    CREATE TABLE ep2 AS
    SELECT e.*, coalesce(p.on_min, 0) AS on_min, n.mean_on,
           coalesce(p.on_min, 0) > 2 * coalesce(n.mean_on, 0) AND n.mean_on IS NOT NULL
               AS pump_busy
    FROM ep e
    LEFT JOIN pump_day p USING (object_id, gallery, picket, day)
    LEFT JOIN pump_norm n USING (object_id, gallery, picket)
    """
)
# Сутки жизни единиц: знаменатель частот.
con.execute(
    f"""
    CREATE TABLE life AS
    SELECT u.object_id, u.gallery, u.picket, d.day::DATE AS day, k.is_day_off = 1 AS day_off
    FROM read_parquet('{UNITS.as_posix()}') u,
         LATERAL (SELECT unnest(generate_series(u.hour_from::DATE, u.hour_to::DATE,
                                                INTERVAL 1 DAY)) AS day) d
    JOIN {cal} k ON k.day = d.day::DATE
    """
)
con.execute(
    f"""
    CREATE TABLE wet AS
    SELECT day,
           sum(precip_mm) OVER w >= {WET_RAIN_MM}
           OR (sum(melt_cm) OVER w >= {WET_MELT_CM} AND max(temp_max) OVER w > 0) AS wet
    FROM read_parquet('{WEATHER.as_posix()}')
    WINDOW w AS (ORDER BY day ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
    """
)

# Критерий: условие на час сигнала `s` и сутки `e`, плюс разрешённые часы в
# будни и в выходные для счёта ритма недели.
CRITERIA = {
    "C0_все_сигналы": ("TRUE", 24, 24),
    "C1_вне_рабочего_окна": ("NOT s.in_work", 24 - (WORK_TO - WORK_FROM), 24),
    "C2_без_коротких_разовых": (
        "NOT (e.work_only AND e.hrs <= 1 AND NOT e.adjacent)",
        24,
        24,
    ),
    "C3_дольше_часа": ("e.hrs >= 2", 24, 24),
    "C4_несколько_суток_подряд": ("e.adjacent", 24, 24),
    "C5_вне_окна_или_долгое_днём": (
        "NOT s.in_work OR e.hrs >= 3 OR e.adjacent",
        24,
        24,
    ),
    "C6_подтверждено_другим_источником": (
        "e.neighbour OR (e.any_sensor = 1 AND e.any_pump = 1) OR e.pump_busy",
        24,
        24,
    ),
    "C7_вне_окна_или_подтверждено": (
        "NOT s.in_work OR e.neighbour OR (e.any_sensor = 1 AND e.any_pump = 1) OR e.pump_busy",
        24,
        24,
    ),
    "C8_вне_окна_и_дольше_часа": (
        "NOT s.in_work AND (e.hrs >= 2 OR e.adjacent)",
        16,
        24,
    ),
    "C9_только_ночь": ("s.at_night", 8, 8),
    "C10_вне_окна_насос_без_датчика": ("NOT s.in_work AND s.from_pump = 1", 16, 24),
}


def evaluate(
    name: str, condition: str, allowed_workday: int, allowed_day_off: int
) -> dict:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE counted AS
        SELECT s.* FROM sig s
        JOIN ep2 e USING (object_id, gallery, picket, day)
        WHERE {condition}
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE pos AS
        SELECT DISTINCT object_id, gallery, picket, day FROM counted
        """
    )
    volume = con.execute(
        f"""
        SELECT count(*), count(*) FILTER (WHERE day < DATE '{TRAIN_END}'),
               (SELECT max(n) FROM (SELECT count(*) n FROM pos GROUP BY object_id))
        FROM pos
        """
    ).fetchone()
    # Ритм недели: засчитанные часы на разрешённый час в будни и в выходные.
    rhythm = con.execute(
        f"""
        SELECT
            (SELECT count(*) FROM counted WHERE NOT day_off)
                / ((SELECT count(*) FROM life WHERE NOT day_off) * {allowed_workday}.0),
            (SELECT count(*) FROM counted WHERE day_off)
                / ((SELECT count(*) FROM life WHERE day_off) * {allowed_day_off}.0)
        """
    ).fetchone()
    weather = con.execute(
        """
        SELECT avg(CASE WHEN p.day IS NOT NULL THEN 1.0 ELSE 0 END) FILTER (WHERE w.wet),
               avg(CASE WHEN p.day IS NOT NULL THEN 1.0 ELSE 0 END) FILTER (WHERE NOT w.wet)
        FROM life l
        JOIN wet w USING (day)
        LEFT JOIN pos p USING (object_id, gallery, picket, day)
        """
    ).fetchone()
    report = con.execute(
        """
        SELECT avg(e.on_min / nullif(e.mean_on, 0)), avg(e.adjacent::INT)
        FROM pos p JOIN ep2 e USING (object_id, gallery, picket, day)
        """
    ).fetchone()
    hours = con.execute(
        "SELECT hour(hour) h, count(*) FROM counted GROUP BY 1 ORDER BY 1"
    ).fetchall()
    total_hours = sum(n for _, n in hours) or 1
    work_peak = sum(n for h, n in hours if WORK_FROM <= h < WORK_TO) / total_hours
    ratio = rhythm[0] / rhythm[1] if rhythm[1] else None
    lift = weather[0] / weather[1] if weather[1] else None
    passes = {
        "volume": volume[1] >= 1000 and volume[2] / max(volume[0], 1) <= 0.5,
        "no_work_week": ratio is not None and 1 / 1.5 <= ratio <= 1.5,
        "weather": lift is not None and lift > 1.0,
    }
    return {
        "criterion": name,
        "positive_unit_days": volume[0],
        "positive_unit_days_train": volume[1],
        "top_object_share": round(volume[2] / max(volume[0], 1), 3),
        "workday_to_day_off_rate": round(ratio, 3) if ratio else None,
        "weather_lift": round(lift, 3) if lift else None,
        "event_rate_wet_pct": round(100 * weather[0], 3),
        "event_rate_dry_pct": round(100 * weather[1], 3),
        "pump_minutes_vs_normal": round(report[0], 2) if report[0] else None,
        "adjacent_day_share": round(report[1], 3),
        "share_of_hours_in_8_16": round(work_peak, 3),
        "passes": passes,
        "passes_all": all(passes.values()),
    }


results = [evaluate(name, *spec) for name, spec in CRITERIA.items()]
passed = [r for r in results if r["passes_all"]]
chosen = None
if passed:
    best = max(r["weather_lift"] for r in passed)
    close = [r for r in passed if r["weather_lift"] >= best * 0.95]
    chosen = max(close, key=lambda r: r["positive_unit_days"])["criterion"]

wet_share = con.execute("SELECT avg(wet::INT) FROM wet").fetchone()[0]
RESULT.write_text(
    json.dumps(
        {"wet_day_share": round(wet_share, 3), "criteria": results, "chosen": chosen},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(f"мокрых суток {100 * wet_share:.1f} %")
print(
    f"{'критерий':38} {'сутки':>6} {'обуч':>6} {'объект':>6} {'будни/вых':>9} "
    f"{'погода':>7} {'насос':>6} {'подряд':>6} {'8-16ч':>6}  отбор"
)
for r in results:
    print(
        f"{r['criterion']:38} {r['positive_unit_days']:6} {r['positive_unit_days_train']:6} "
        f"{r['top_object_share']:6} {r['workday_to_day_off_rate']:9} {r['weather_lift']:7} "
        f"{r['pump_minutes_vs_normal']!s:>6} {r['adjacent_day_share']:6} "
        f"{r['share_of_hours_in_8_16']:6}  {'да' if r['passes_all'] else r['passes']}"
    )
print(f"выбран: {chosen}")
