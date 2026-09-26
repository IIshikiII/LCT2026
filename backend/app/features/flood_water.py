"""Разметка суток пикета: вода или плановая проверка. ADR 0012, ADR 0013.

Событие это московские сутки пикета с сигналом «Затоплен» насоса или «Не
замкнут» датчика затопления. Разметку делает классификатор из
`ml/flood/11_pu_label.py`, файл `ARTIFACTS_DIR/flood_risk/pu_classifier.joblib`:

1. Сигнал ночью (22:00–6:00) или в нерабочий день значит вода. Проверок в это
   время почти нет.
2. Остальные сутки получают оценку классификатора по 17 признакам события.
   Поправка Elkan и Noto переводит оценку в вероятность воды:
   `p = min(score / c, 1)`. Сутки с `p` не ниже границы это вода.
3. Классификатора нет значит работает только правило времени, остальные сутки
   считаются проверкой.

## Признаки события

Имена и смысл совпадают с `11_pu_label.py`. Отличия от обучения:

- обычный уровень работы насоса считается по году до суток события, а не по
  всем обучающим годам;
- окна вокруг первого сигнала не выходят за конец суток. Сигнал после 22:00
  размечает правило времени, поэтому на итог это не влияет;
- смены состояния насоса считаются по записям, как в признаках модели.

Все окна кончаются концом размечаемых суток. Сутки размечаются только после
того, как закончились, поэтому разметка не видит будущего относительно точки
расчёта модели.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.features.flood import (
    DIRECTION,
    FLOOD_SENSOR_OPEN,
    FLOODED,
    LABEL_TYPES,
    MAX_ON_HOURS,
    PUMP_ALL_RUNNING,
    PUMP_ON,
    PUMP_UNAVAILABLE,
    STATE_TYPES,
    TIMEZONE,
    WEATHER_AREA,
    local_day,
    point_of,
)
from app.tables import flood_water_day

log = logging.getLogger(__name__)

# Первый прогон размечает год: столько смотрит самое длинное окно модели.
FIRST_RUN_DAYS = 366
# Последние сутки размечаются заново на каждом прогоне: сигнал мог прийти в
# журнал с опозданием.
RELABEL_DAYS = 2
# Обычный уровень работы насоса считается по этому числу суток до события.
NORM_DAYS = 365
NIGHT_FROM, NIGHT_TO = 22, 6

FEATURES = (
    "n_hours",
    "n_moments",
    "has_sensor",
    "has_pump",
    "pump_on_ratio",
    "pump_changes_ratio",
    "pump_all_running",
    "pump_unavailable",
    "neighbour_units",
    "signal_yesterday",
    "precip_3d_mm",
    "melt_3d_cm",
    "pump_on_before_2h_ratio",
    "pump_on_after_2h_ratio",
    "span_minutes",
    "n_episodes",
    "other_units_within_1h",
)

_CACHE: dict[str, Any] = {}


@dataclass(frozen=True)
class WaterDay:
    facility_id: str
    day: date
    is_water: bool
    is_labelled: bool
    p_water: float | None


def classifier() -> dict[str, Any] | None:
    """Классификатор из `ARTIFACTS_DIR`, один раз на процесс."""
    if "pu" not in _CACHE:
        from app.ml.tracking import PU_CLASSIFIER_FILENAME, load_artifact

        _CACHE["pu"] = load_artifact(DIRECTION, PU_CLASSIFIER_FILENAME)
    payload = _CACHE["pu"]
    return payload if isinstance(payload, dict) else None


def reset_cache() -> None:
    """Забывает классификатор. Нужен тесту, который подменяет файл."""
    _CACHE.clear()


def _bound(day: date) -> datetime:
    """Начало московских суток в UTC."""
    return point_of(datetime(day.year, day.month, day.day, 12, tzinfo=UTC))


_EVENT_SQL = f"""
WITH sig AS (
    SELECT e.facility_id, e.sensor_id, e.alarm_type, e.occurred_at,
           (e.occurred_at AT TIME ZONE '{TIMEZONE}') AS ts
    FROM alarm_event e
    WHERE e.alarm_type = ANY(:label)
      AND e.occurred_at >= :lo - interval '1 day' AND e.occurred_at < :hi
),
sd AS (SELECT *, ts::date AS day FROM sig),
gaps AS (
    SELECT facility_id, day,
           ts - lag(ts) OVER (PARTITION BY facility_id, day ORDER BY ts) AS gap
    FROM sd
),
d AS (
    SELECT facility_id, day,
           count(DISTINCT date_trunc('hour', ts)) AS n_hours,
           count(DISTINCT (sensor_id, occurred_at)) AS n_moments,
           max((alarm_type = :sensor_open)::int) AS has_sensor,
           max((alarm_type = :flooded)::int) AS has_pump,
           bool_or(extract(hour FROM ts) >= {NIGHT_FROM} OR extract(hour FROM ts) < {NIGHT_TO})
               AS night,
           min(occurred_at) AS first_at,
           extract(epoch FROM max(ts) - min(ts)) / 60.0 AS span_minutes
    FROM sd GROUP BY 1, 2
),
ep AS (
    SELECT facility_id, day, 1 + count(*) FILTER (WHERE gap > interval '30 minutes') AS n_episodes
    FROM gaps GROUP BY 1, 2
),
fac AS (SELECT DISTINCT facility_id FROM d WHERE day >= :d_from),
st AS (
    SELECT e.facility_id, e.alarm_type, e.occurred_at AS a,
           lead(e.occurred_at) OVER (
               PARTITION BY e.sensor_id ORDER BY e.occurred_at, e.alarm_type) AS nxt
    FROM alarm_event e JOIN fac USING (facility_id)
    WHERE e.alarm_type = ANY(:state)
      AND e.occurred_at >= :lo - interval '{NORM_DAYS + 1} days' AND e.occurred_at < :hi
),
iv AS (
    SELECT facility_id, a,
           least(coalesce(nxt, a + interval '{MAX_ON_HOURS} hours'),
                 a + interval '{MAX_ON_HOURS} hours', :hi) AS b
    FROM st WHERE alarm_type = :on
),
on_day AS (
    SELECT iv.facility_id, g.day::date AS day,
           sum(greatest(extract(epoch FROM
               least(iv.b, (g.day::date + 1)::timestamp AT TIME ZONE '{TIMEZONE}')
               - greatest(iv.a, g.day::date::timestamp AT TIME ZONE '{TIMEZONE}')), 0)) / 60.0
               AS on_min
    FROM iv, LATERAL generate_series(
        (iv.a AT TIME ZONE '{TIMEZONE}')::date, (iv.b AT TIME ZONE '{TIMEZONE}')::date,
        interval '1 day') AS g(day)
    GROUP BY 1, 2
),
cnt_day AS (
    SELECT e.facility_id, (e.occurred_at AT TIME ZONE '{TIMEZONE}')::date AS day,
           count(*) FILTER (WHERE e.alarm_type = ANY(:state)) AS changes,
           count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER (WHERE e.alarm_type = :allp)
               AS allp,
           count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER (WHERE e.alarm_type = :unavail)
               AS unavail
    FROM alarm_event e JOIN fac USING (facility_id)
    WHERE e.alarm_type = ANY(:pump_types)
      AND e.occurred_at >= :lo - interval '{NORM_DAYS + 1} days' AND e.occurred_at < :hi
    GROUP BY 1, 2
),
pday AS (
    SELECT coalesce(o.facility_id, c.facility_id) AS facility_id,
           coalesce(o.day, c.day) AS day,
           coalesce(o.on_min, 0) AS on_min, coalesce(c.changes, 0) AS changes,
           coalesce(c.allp, 0) AS allp, coalesce(c.unavail, 0) AS unavail
    FROM on_day o FULL JOIN cnt_day c ON c.facility_id = o.facility_id AND c.day = o.day
),
wd AS (
    SELECT (observed_at AT TIME ZONE '{TIMEZONE}')::date AS day,
           sum(coalesce(precip_mm, 0)) AS precip,
           (array_agg(snow_depth_m ORDER BY observed_at DESC))[1] AS snow_end
    FROM weather_hourly
    WHERE district = :area
      AND observed_at >= :lo - interval '4 days' AND observed_at < :hi
    GROUP BY 1
),
wm AS (
    SELECT day, precip,
           greatest(0, 100 * (lag(snow_end) OVER (ORDER BY day) - snow_end)) AS melt
    FROM wd
),
w3 AS (
    SELECT day, sum(precip) OVER w AS precip_3d_mm, sum(coalesce(melt, 0)) OVER w AS melt_3d_cm
    FROM wm WINDOW w AS (ORDER BY day ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
)
SELECT d.facility_id, d.day, d.night,
       d.n_hours, d.n_moments, d.has_sensor, d.has_pump,
       coalesce(p.on_min, 0) / greatest(coalesce(nm.mean_on, 0), 1) AS pump_on_ratio,
       coalesce(p.changes, 0) / greatest(coalesce(nm.mean_changes, 0), 1) AS pump_changes_ratio,
       coalesce(p.allp, 0) AS pump_all_running,
       coalesce(p.unavail, 0) AS pump_unavailable,
       (SELECT count(DISTINCT o.facility_id) FROM d o
         JOIN facility fo ON fo.id = o.facility_id
         WHERE fo.collector = f.collector AND o.day = d.day
           AND o.facility_id <> d.facility_id) AS neighbour_units,
       EXISTS (SELECT 1 FROM d y WHERE y.facility_id = d.facility_id
                 AND y.day = d.day - 1)::int AS signal_yesterday,
       coalesce(w.precip_3d_mm, 0) AS precip_3d_mm,
       coalesce(w.melt_3d_cm, 0) AS melt_3d_cm,
       coalesce((SELECT sum(greatest(extract(epoch FROM
                    least(iv.b, h.at0) - greatest(iv.a, h.at0 - interval '2 hours')), 0)) / 60.0
                 FROM iv WHERE iv.facility_id = d.facility_id), 0)
           / greatest(coalesce(nm.mean_on, 0) / 12, 1) AS pump_on_before_2h_ratio,
       coalesce((SELECT sum(greatest(extract(epoch FROM
                    least(iv.b, h.at0 + interval '2 hours', h.day_end) - greatest(iv.a, h.at0)),
                    0)) / 60.0
                 FROM iv WHERE iv.facility_id = d.facility_id), 0)
           / greatest(coalesce(nm.mean_on, 0) / 12, 1) AS pump_on_after_2h_ratio,
       d.span_minutes,
       coalesce(ep.n_episodes, 1) AS n_episodes,
       (SELECT count(DISTINCT r.facility_id) FROM sd r
         JOIN facility fr ON fr.id = r.facility_id
         WHERE fr.collector = f.collector AND r.facility_id <> d.facility_id
           AND r.occurred_at BETWEEN d.first_at - interval '1 hour'
                                 AND d.first_at + interval '1 hour'
           AND r.occurred_at < h.day_end) AS other_units_within_1h
FROM d
JOIN facility f ON f.id = d.facility_id
CROSS JOIN LATERAL (
    SELECT date_trunc('hour', d.first_at) AS at0,
           (d.day + 1)::timestamp AT TIME ZONE '{TIMEZONE}' AS day_end
) h
LEFT JOIN ep ON ep.facility_id = d.facility_id AND ep.day = d.day
LEFT JOIN pday p ON p.facility_id = d.facility_id AND p.day = d.day
LEFT JOIN LATERAL (
    SELECT avg(n.on_min) AS mean_on, avg(n.changes) AS mean_changes
    FROM pday n
    WHERE n.facility_id = d.facility_id AND n.day < d.day
      AND n.day >= d.day - {NORM_DAYS}
) nm ON true
LEFT JOIN w3 w ON w.day = d.day
WHERE d.day >= :d_from AND d.day < :d_to
ORDER BY d.facility_id, d.day
"""


def event_rows(conn: Connection, d_from: date, d_to: date) -> list[dict[str, Any]]:
    """Признаки событий за московские сутки `d_from … d_to − 1`."""
    params = {
        "lo": _bound(d_from),
        "hi": _bound(d_to),
        "d_from": d_from,
        "d_to": d_to,
        "label": list(LABEL_TYPES),
        "sensor_open": FLOOD_SENSOR_OPEN,
        "flooded": FLOODED,
        "state": list(STATE_TYPES),
        "on": PUMP_ON,
        "allp": PUMP_ALL_RUNNING,
        "unavail": PUMP_UNAVAILABLE,
        "pump_types": [*STATE_TYPES, PUMP_ALL_RUNNING, PUMP_UNAVAILABLE],
        "area": WEATHER_AREA,
    }
    return [dict(row) for row in conn.execute(text(_EVENT_SQL), params).mappings()]


def classify(rows: list[dict[str, Any]], model: dict[str, Any] | None) -> list[WaterDay]:
    """Размечает события: правило времени, затем классификатор."""
    from app.features.calendar_ru import is_day_off

    labelled = [bool(row["night"]) or is_day_off(row["day"]) for row in rows]
    p_water: list[float | None] = [None] * len(rows)
    if model is not None and rows:
        import numpy as np

        names = list(model["features"])
        matrix = np.asarray(
            [[float(row[name]) for name in names] for row in rows], dtype=np.float32
        )
        scores = model["booster"].predict(matrix)
        c = float(model["c"])
        p_water = [min(float(score) / c, 1.0) for score in scores]
    cutoff = float(model["cutoff"]) if model is not None else 1.0
    return [
        WaterDay(
            facility_id=str(row["facility_id"]),
            day=row["day"],
            is_labelled=is_labelled,
            is_water=is_labelled or (p is not None and p >= cutoff),
            p_water=1.0 if is_labelled else p,
        )
        for row, is_labelled, p in zip(rows, labelled, p_water, strict=True)
    ]


def refresh(conn: Connection, at: datetime) -> int:
    """Размечает законченные сутки до точки расчёта `at`. Отдаёт число строк.

    Сутки точки и позже не размечаются: они не кончились. Первый прогон
    размечает год, следующие продолжают с последних размеченных суток.
    """
    today = local_day(point_of(at))
    last = conn.execute(text("SELECT max(day) FROM flood_water_day")).scalar()
    first_run = today - timedelta(days=FIRST_RUN_DAYS)
    d_from = max(last - timedelta(days=RELABEL_DAYS - 1), first_run) if last else first_run
    if d_from >= today:
        return 0

    days = classify(event_rows(conn, d_from, today), classifier())
    if not days:
        return 0
    stamp = datetime.now(UTC)
    statement = pg_insert(flood_water_day).values(
        [
            {
                "facility_id": item.facility_id,
                "day": item.day,
                "is_water": item.is_water,
                "is_labelled": item.is_labelled,
                "p_water": item.p_water,
                "labelled_at": stamp,
            }
            for item in days
        ]
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=["facility_id", "day"],
            set_={
                "is_water": statement.excluded.is_water,
                "is_labelled": statement.excluded.is_labelled,
                "p_water": statement.excluded.p_water,
                "labelled_at": statement.excluded.labelled_at,
            },
        )
    )
    water = sum(item.is_water for item in days)
    log.info(
        "сутки подтопления размечены",
        extra={"days": len(days), "water": water, "from": str(d_from), "to": str(today)},
    )
    return len(days)
