"""Факты эпизода для экспертных правил пожара. ADR 0016.

Модуль читает базу и отдаёт `app.ml.fire_rules.Facts` одного пикета на момент
расчёта. Правила уровня живут отдельно, в `app/ml/fire_rules.py`: тот же код
размечает историю в `ml/fire/15_expert_history.py`.

Окно наблюдения это 24 часа до момента расчёта. Всё, что позже момента
расчёта, запрос не видит. Соседей по времени сигнал видит только в пределах
окна: сигнал за пять минут до расчёта не знает, что будет через десять.

Соседи. Пикет это `facility`, объект это `collector`, комплекс это
`district`. **Сейчас** у пикета нет координаты вдоль трассы, поэтому соседом
считается любой пикет того же объекта. На истории соседство бралось в
пределах 50 м (`ml/fire/15_expert_history.py`). Переход схемы на тройку
«объект, галерея, пикет» вернёт расстояние.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features.calendar_ru import is_day_off
from app.features.flood import MOSCOW
from app.ml.fire_rules import OBJECT_BURN_IN_DAYS, SENSOR_BURN_IN_DAYS, Facts

SMOKE_DETECTED = "SMOKE_DETECTED"
HEAT_OPEN = "HEAT_OPEN"
TEMP_HIGH = "TEMP_HIGH"
PHASE_OFF = "PHASE_OFF"
SIGNAL_TYPES = (SMOKE_DETECTED, HEAT_OPEN, TEMP_HIGH)

SENSOR_SMOKE = "SMOKE"
SENSOR_HEAT = "HEAT"
SENSOR_TEMPERATURE = "TEMPERATURE"
SENSOR_METHANE = "METHANE"
FIRE_SENSOR_TYPES = (SENSOR_SMOKE, SENSOR_HEAT, SENSOR_TEMPERATURE)

METRIC_TEMPERATURE = "temperature_c"
METRIC_METHANE = "methane_pct"

WINDOW_HOURS = 24
BURST_NEIGHBOURS = 2
BURST_MINUTES = 60
SPREAD_MINUTES = 30
HEAT_MINUTES = 60
TEMP_AROUND_HOURS = 2
TEMP_NORM_DAYS = 30
POWER_MINUTES = 60
WORK_HOURS = (8, 16)
# Ряд сигналов пикета для карточки: шесть окон по четыре часа, старое первым.
BUCKET_HOURS = 4
BUCKETS = WINDOW_HOURS // BUCKET_HOURS


@dataclass(frozen=True)
class Signal:
    facility_id: str
    sensor_id: str
    kind: str
    at: datetime


def local(at: datetime) -> datetime:
    return at.astimezone(MOSCOW)


def _neighbours(signal: Signal, others: Sequence[Signal], minutes: int) -> set[str]:
    """Другие пикеты объекта с сигналом в пределах `minutes` в те же сутки."""
    day = local(signal.at).date()
    span = timedelta(minutes=minutes)
    return {
        o.facility_id
        for o in others
        if o.facility_id != signal.facility_id
        and abs(o.at - signal.at) <= span
        and local(o.at).date() == day
    }


def is_burst(signal: Signal, others: Sequence[Signal]) -> bool:
    return len(_neighbours(signal, others, BURST_MINUTES)) > BURST_NEIGHBOURS


def off_hours(at: datetime) -> bool:
    moment = local(at)
    return is_day_off(moment.date()) or moment.hour < 6 or moment.hour >= 22


def work_hours(at: datetime) -> bool:
    moment = local(at)
    return not is_day_off(moment.date()) and WORK_HOURS[0] <= moment.hour < WORK_HOURS[1]


def _signals(conn: Connection, facility_id: str, at: datetime) -> tuple[list[Signal], str, str]:
    row = conn.execute(
        text("SELECT collector, district FROM facility WHERE id = :id"), {"id": facility_id}
    ).first()
    if row is None:
        return [], "", ""
    collector, district = str(row[0]), str(row[1])
    rows = conn.execute(
        text(
            """
            SELECT e.facility_id, e.sensor_id, e.alarm_type, e.occurred_at
            FROM alarm_event e JOIN facility f ON f.id = e.facility_id
            WHERE f.collector = :collector AND e.alarm_type = ANY(:types)
              AND e.occurred_at >= :start AND e.occurred_at < :at
            ORDER BY e.occurred_at
            """
        ),
        {
            "collector": collector,
            "types": list(SIGNAL_TYPES),
            "start": at - timedelta(hours=WINDOW_HOURS + 1),
            "at": at,
        },
    ).all()
    return [Signal(str(r[0]), str(r[1]), str(r[2]), r[3]) for r in rows], collector, district


def _temp_rise(conn: Connection, collector: str, moment: datetime, at: datetime) -> float:
    row = conn.execute(
        text(
            """
            WITH r AS (
                SELECT s.observed_at, s.value FROM sensor_reading s
                JOIN facility f ON f.id = s.facility_id
                WHERE f.collector = :collector AND s.metric = :metric
                  AND s.observed_at >= :norm_start AND s.observed_at < :end
            )
            SELECT
                (SELECT max(value) FROM r WHERE observed_at >= :around_start),
                (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value) FROM r
                 WHERE observed_at < :around_start)
            """
        ),
        {
            "collector": collector,
            "metric": METRIC_TEMPERATURE,
            "norm_start": moment - timedelta(days=TEMP_NORM_DAYS),
            "around_start": moment - timedelta(hours=TEMP_AROUND_HOURS),
            "end": min(moment + timedelta(hours=TEMP_AROUND_HOURS), at),
        },
    ).first()
    if row is None or row[0] is None or row[1] is None:
        return 0.0
    return max(0.0, float(row[0]) - float(row[1]))


def _power_off(conn: Connection, district: str, moment: datetime, at: datetime) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1 FROM alarm_event e JOIN facility f ON f.id = e.facility_id
            WHERE f.district = :district AND e.alarm_type = :kind
              AND e.occurred_at BETWEEN :start AND :end LIMIT 1
            """
        ),
        {
            "district": district,
            "kind": PHASE_OFF,
            "start": moment - timedelta(minutes=POWER_MINUTES),
            "end": min(moment + timedelta(minutes=POWER_MINUTES), at),
        },
    ).first()
    return row is not None


def _gas(conn: Connection, collector: str, at: datetime) -> tuple[float, bool]:
    """Наибольший метан объекта за окно вне окон ППР и ТО."""
    row = conn.execute(
        text(
            """
            SELECT s.observed_at, s.value FROM sensor_reading s
            JOIN facility f ON f.id = s.facility_id
            WHERE f.collector = :collector AND s.metric = :metric
              AND s.observed_at >= :start AND s.observed_at < :at
              AND s.value BETWEEN 0 AND 100
              AND NOT EXISTS (
                  SELECT 1 FROM maintenance_window w
                  WHERE w.collector = :collector
                    AND (s.observed_at AT TIME ZONE 'Europe/Moscow')::date
                        BETWEEN w.starts_on AND w.ends_on
              )
            ORDER BY s.value DESC LIMIT 1
            """
        ),
        {
            "collector": collector,
            "metric": METRIC_METHANE,
            "start": at - timedelta(hours=WINDOW_HOURS),
            "at": at,
        },
    ).first()
    if row is None:
        return 0.0, False
    return float(row[1]), not work_hours(row[0])


def _cooling(
    conn: Connection, collector: str, sensors: set[str], at: datetime
) -> tuple[bool, bool, int]:
    """Охлаждение датчика и объекта на момент расчёта."""
    today = local(at).date()
    sensor_left = 0
    sensor_cooling = False
    if sensors:
        rows = conn.execute(
            text(
                """
                SELECT s.id, coalesce(s.installed_at,
                    (SELECT min(e.occurred_at)::date FROM alarm_event e WHERE e.sensor_id = s.id))
                FROM sensor s WHERE s.id = ANY(:ids)
                """
            ),
            {"ids": sorted(sensors)},
        ).all()
        born = [r[1] for r in rows if r[1] is not None]
        if born and len(born) == len(sensors):
            oldest = (today - min(born)).days
            sensor_cooling = oldest < SENSOR_BURN_IN_DAYS
            sensor_left = max(0, SENSOR_BURN_IN_DAYS - oldest)
    row = conn.execute(
        text(
            """
            SELECT c.commissioning_until,
                (SELECT min(s.installed_at) FROM sensor s JOIN facility f ON f.id = s.facility_id
                 WHERE f.collector = c.code AND s.sensor_type = ANY(:types))
            FROM collector c WHERE c.code = :collector
            """
        ),
        {"collector": collector, "types": list(FIRE_SENSOR_TYPES)},
    ).first()
    object_cooling, object_left = False, 0
    if row is not None:
        until: date | None = row[0]
        first: date | None = row[1]
        if until is not None and until >= today:
            object_cooling, object_left = True, (until - today).days
        elif first is not None and (today - first).days < OBJECT_BURN_IN_DAYS:
            object_cooling = True
            object_left = OBJECT_BURN_IN_DAYS - (today - first).days
    left = sensor_left if sensor_cooling else object_left
    return sensor_cooling, object_cooling, left


@dataclass(frozen=True)
class Episode:
    facts: Facts
    signals_24h: int
    burst_signals_24h: int
    hours_since_signal: float
    buckets: tuple[int, ...] = (0,) * BUCKETS


def _buckets(signals: Sequence[Signal], at: datetime) -> tuple[int, ...]:
    counts = [0] * BUCKETS
    for s in signals:
        back = int((at - s.at).total_seconds() // (BUCKET_HOURS * 3600))
        if 0 <= back < BUCKETS:
            counts[BUCKETS - 1 - back] += 1
    return tuple(counts)


def episode(conn: Connection, facility_id: str, at: datetime) -> Episode:
    """Факты пикета за окно 24 часа до `at`."""
    all_signals, collector, district = _signals(conn, facility_id, at)
    window_start = at - timedelta(hours=WINDOW_HOURS)
    own = [s for s in all_signals if s.facility_id == facility_id and s.at >= window_start]
    single = [s for s in own if not is_burst(s, all_signals)]
    walk_days = {local(s.at).date() for s in all_signals if is_burst(s, all_signals)}
    gas_pct, gas_off = _gas(conn, collector, at) if collector else (0.0, False)

    if not single:
        facts = Facts(burst_only=bool(own), gas_pct=gas_pct, gas_off_hours=gas_off)
        return Episode(facts, len(own), len(own), _hours_since(own, at), _buckets(own, at))

    first = single[0].at
    heat_independent = any(
        s.kind == SMOKE_DETECTED
        and any(
            o.kind != SMOKE_DETECTED
            and o.sensor_id != s.sensor_id
            and abs(o.at - s.at) <= timedelta(minutes=HEAT_MINUTES)
            for o in all_signals
        )
        for s in single
    )
    spread = any(_neighbours(s, all_signals, SPREAD_MINUTES) for s in single)
    night = any(off_hours(s.at) and local(s.at).date() not in walk_days for s in single)
    sensor_cooling, object_cooling, left = _cooling(
        conn, collector, {s.sensor_id for s in single}, at
    )
    facts = Facts(
        signal=True,
        burst_only=False,
        smoke=any(s.kind == SMOKE_DETECTED for s in single),
        heat_independent=heat_independent,
        spread=spread,
        temp_rise_c=_temp_rise(conn, collector, first, at),
        off_hours_no_walk=night,
        power_off=_power_off(conn, district, first, at),
        gas_pct=gas_pct,
        gas_off_hours=gas_off,
        sensor_cooling=sensor_cooling,
        object_cooling=object_cooling,
        cooling_days_left=left,
    )
    return Episode(
        facts, len(own), len(own) - len(single), _hours_since(own, at), _buckets(own, at)
    )


def _hours_since(signals: Sequence[Signal], at: datetime) -> float:
    if not signals:
        return float(WINDOW_HOURS)
    return round((at - signals[-1].at).total_seconds() / 3600, 2)


def to_vector(item: Episode) -> dict[str, float]:
    """Вектор для `prediction.features`: факты числами и счётчики окна."""
    vector = {name: float(value) for name, value in asdict(item.facts).items()}
    vector["signals_24h"] = float(item.signals_24h)
    vector["burst_signals_24h"] = float(item.burst_signals_24h)
    vector["hours_since_signal"] = float(item.hours_since_signal)
    for index, count in enumerate(item.buckets):
        vector[f"signals_bucket_{index}"] = float(count)
    return vector


def from_vector(vector: dict[str, float]) -> Facts:
    """Факты из сохранённого вектора. Карточку старого прогноза строит тот же код."""
    fields = Facts.__dataclass_fields__
    values: dict[str, object] = {}
    for name, spec in fields.items():
        raw = vector.get(name) or 0.0
        if spec.type in ("bool", bool):
            values[name] = bool(raw)
        elif spec.type in ("int", int):
            values[name] = int(raw)
        else:
            values[name] = float(raw)
    return Facts(**values)  # type: ignore[arg-type]
