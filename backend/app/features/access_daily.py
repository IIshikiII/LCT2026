"""Суточные признаки направления «несанкционированный доступ» на участке.

Имена и смысл совпадают с `ml/access/panel.py` буква в букву: по имени модель
берёт столбец. Расхождение в имени модель не заметит, а прогноз станет мусором.
Правило события лежит в `app/features/access.py`.

## Сутки

Обучение ставит точку расчёта на 00:00 суток T и прогнозирует сутки T. Сутки
k в окне признака это отрезок `[at − (k + 1) × 24 ч, at − k × 24 ч)`. Точка в
00:00 даёт ровно календарные сутки обучения. Точка внутри суток даёт те же
окна, сдвинутые к ней: конвейер видит свежие тревоги, не дожидаясь полуночи.
Календарь берётся по московской дате точки расчёта.

Все окна кончаются строго на `at`: `occurred_at < :at`. Тест
`tests/test_features_no_leak.py` проверяет это для каждого признака реестра.

## Срок жизни участка

Участок живёт с `facility.commissioned_at`. Приёмник кладёт туда первые сутки
участка из `ml/access/out/access_units.parquet`: они уже отсекают пусконаладку.
Пустое поле значит первую запись участка в `alarm_event`.

## Признаки сети

`net_event_share_*` и `date_event_share_prev_years` одинаковы для всех участков
на одну точку расчёта. Конвейер считает их один раз на прогон: результат
запоминается на пару «соединение и точка расчёта». Прогон идёт одним
соединением, другой прогон берёт другое.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features import registry
from app.features.access import ACCESS_ALARM_TYPES, event_ctes, event_params
from app.features.calendar_ru import day_off_chain, is_day_off

DIRECTION = "UNAUTHORIZED_ACCESS"
DAY = timedelta(hours=24)
MOSCOW = timedelta(hours=3)
# Окно дат прошлых лет вокруг прогнозируемых суток, как в `panel.py`.
SAME_DATE_DAYS = 3

_UNIT = "e.facility_id = :facility_id"
_OBJECT = "e.facility_id IN (SELECT id FROM facility WHERE collector = :collector)"
_NETWORK = "TRUE"


def _local_date(at: datetime) -> date:
    """Московская дата точки расчёта. Москва живёт без перехода на летнее время."""
    return (at.astimezone(UTC) + MOSCOW).date()


def _scalar(conn: Connection, sql: str, params: dict[str, Any]) -> Any:
    row = conn.execute(text(sql), params).fetchone()
    return row[0] if row else None


def _query(
    conn: Connection,
    scope: str,
    source: str,
    select: str,
    start: datetime,
    at: datetime,
    **params: Any,
) -> Any:
    """Считает `select` по моментам `source` в `[start, at)`.

    `source` это `access_moment` (все тревоги доступа) или `event_moment`
    (моменты событий). Оба определяет `event_ctes`.
    """
    sql = f"WITH {event_ctes(scope)} SELECT {select} FROM {source} m"
    return _scalar(conn, sql, {**event_params(start, at), "at": at, **params})


def _day_index(column: str = "m.ts") -> str:
    """Номер суток от точки расчёта: 0 это последние 24 часа."""
    return f"floor(extract(epoch FROM (:at - {column})) / 86400)"


def life_start(conn: Connection, facility_id: str, at: datetime) -> datetime:
    """Начало жизни участка. Точка расчёта, если участок ещё не писал."""
    value = _scalar(
        conn,
        "SELECT coalesce("
        "  (SELECT commissioned_at::timestamp AT TIME ZONE 'Europe/Moscow' "
        "   FROM facility WHERE id = :facility_id),"
        "  (SELECT min(occurred_at) FROM alarm_event "
        "   WHERE facility_id = :facility_id AND occurred_at < :at))",
        {"facility_id": facility_id, "at": at},
    )
    if value is None or value >= at:
        return at
    return value


def _age_days(conn: Connection, facility_id: str, at: datetime) -> int:
    """Полных суток жизни участка до точки расчёта: `unit_age_days` обучения."""
    return int((at - life_start(conn, facility_id, at)) // DAY)


def _collector(conn: Connection, facility_id: str) -> str | None:
    return _scalar(conn, "SELECT collector FROM facility WHERE id = :f", {"f": facility_id})


# --- Участок -----------------------------------------------------------


def _count(conn: Connection, facility_id: str, at: datetime, days: int, source: str) -> int:
    value = _query(conn, _UNIT, source, "count(*)", at - days * DAY, at, facility_id=facility_id)
    return int(value or 0)


def _day_share(conn: Connection, facility_id: str, at: datetime, days: int, source: str) -> float:
    value = _query(
        conn,
        _UNIT,
        source,
        f"count(DISTINCT {_day_index()})",
        at - days * DAY,
        at,
        facility_id=facility_id,
    )
    return int(value or 0) / days


def _last(conn: Connection, facility_id: str, at: datetime, source: str) -> datetime | None:
    start = life_start(conn, facility_id, at)
    return _query(conn, _UNIT, source, "max(m.ts)", start, at, facility_id=facility_id)


def _days_since(conn: Connection, facility_id: str, at: datetime, source: str) -> int:
    """Суток с последнего момента. Без моментов это возраст участка плюс одни сутки."""
    last = _last(conn, facility_id, at, source)
    if last is None:
        return _age_days(conn, facility_id, at) + 1
    return int((at - last) // DAY) + 1


def _hours_since(conn: Connection, facility_id: str, at: datetime, source: str) -> int:
    """Часов от начала часа последнего момента. Без моментов это 24 часа на каждые
    сутки жизни участка плюс одни сутки."""
    last = _last(conn, facility_id, at, source)
    if last is None:
        return 24 * (_age_days(conn, facility_id, at) + 1)
    hour = last.replace(minute=0, second=0, microsecond=0)
    return int((at - hour) // timedelta(hours=1))


def _event_week_ago(conn: Connection, facility_id: str, at: datetime) -> int:
    """Было ли событие в сутки T − 7: номер суток 6 от точки расчёта."""
    start = at - 7 * DAY
    value = _query(
        conn, _UNIT, "event_moment", "count(*)", start, start + DAY, facility_id=facility_id
    )
    return int((value or 0) > 0)


def _n_channels(conn: Connection, facility_id: str, at: datetime) -> int:
    """Каналов доступа, которые хоть раз подали сигнал на участке."""
    value = _scalar(
        conn,
        "SELECT count(DISTINCT sensor_id) FROM alarm_event "
        "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) AND occurred_at < :at",
        {"facility_id": facility_id, "types": list(ACCESS_ALARM_TYPES), "at": at},
    )
    return int(value or 0)


# --- Режим охраны ------------------------------------------------------


def _disarm_hours(conn: Connection, facility_id: str, at: datetime, days: int) -> float:
    """Часов без охраны внутри окна. Незакрытое окно снятия обрезается на `at`."""
    sql = (
        f"WITH {event_ctes(_UNIT)} "
        "SELECT coalesce(sum(extract(epoch FROM ("
        "  least(t_to, :at) - greatest(t_from, :start)))) / 3600.0, 0) "
        "FROM disarm_window WHERE least(t_to, :at) > greatest(t_from, :start)"
    )
    start = at - days * DAY
    value = _scalar(
        conn, sql, {**event_params(start, at), "at": at, "start": start, "facility_id": facility_id}
    )
    return float(value or 0.0)


def _hours_since_guard_change(conn: Connection, facility_id: str, at: datetime) -> float:
    """Часов с последней смены режима охраны. Смен не было значит пусто.

    Сменой считается снятие с охраны и постановка после снятия. Первая
    постановка без снятия перед ней сменой не считается, как в `panel.py`.
    """
    sql = (
        f"WITH {event_ctes(_UNIT)} "
        "SELECT max(ts) FROM ("
        "  SELECT ts, alarm_type, lag(ts) OVER (ORDER BY ts) AS before FROM guard_step"
        ") s WHERE alarm_type = :disarmed OR before IS NOT NULL"
    )
    last = _scalar(conn, sql, {**event_params(at, at), "facility_id": facility_id})
    if last is None:
        return math.nan
    hour = last.replace(minute=0, second=0, microsecond=0)
    return float((at - hour) // timedelta(hours=1))


# --- Объект ------------------------------------------------------------


def _object_alarms(conn: Connection, facility_id: str, at: datetime, days: int) -> int:
    """Моменты тревоги на всех участках объекта, этот участок входит."""
    collector = _collector(conn, facility_id)
    if collector is None:
        return 0
    value = _query(
        conn, _OBJECT, "access_moment", "count(*)", at - days * DAY, at, collector=collector
    )
    return int(value or 0)


def _object_units_alarmed(conn: Connection, facility_id: str, at: datetime, days: int) -> int:
    """Сумма по суткам окна: сколько участков объекта тревожили в эти сутки."""
    collector = _collector(conn, facility_id)
    if collector is None:
        return 0
    value = _query(
        conn,
        _OBJECT,
        "access_moment",
        f"count(DISTINCT (m.facility_id, {_day_index()}))",
        at - days * DAY,
        at,
        collector=collector,
    )
    return int(value or 0)


# --- Сеть --------------------------------------------------------------

_NETWORK_CACHE: dict[tuple[Any, str, datetime], float] = {}


def _per_run(conn: Connection, name: str, at: datetime, compute: Callable[[], float]) -> float:
    """Запоминает признак сети на пару «соединение и точка расчёта».

    Хранится одна последняя пара. Ключ держит само соединение, поэтому его
    адрес не достанется другому соединению, пока запись жива.
    """
    key = (conn, name, at)
    if key not in _NETWORK_CACHE:
        if _NETWORK_CACHE and next(iter(_NETWORK_CACHE))[::2] != (conn, at):
            _NETWORK_CACHE.clear()
        _NETWORK_CACHE[key] = compute()
    return _NETWORK_CACHE[key]


def reset_cache() -> None:
    _NETWORK_CACHE.clear()


_ALIVE = (
    "SELECT coalesce(f.commissioned_at::timestamp AT TIME ZONE 'Europe/Moscow', "
    "  (SELECT min(occurred_at) FROM alarm_event e "
    "   WHERE e.facility_id = f.id AND e.occurred_at < :at)) AS born "
    "FROM facility f WHERE f.is_active"
)


def _net_event_share(conn: Connection, at: datetime, days: int) -> float:
    """Доля пар «участок и сутки» с событием за окно, по всей сети."""

    def compute() -> float:
        start = at - days * DAY
        events = _query(
            conn,
            _NETWORK,
            "event_moment",
            f"count(DISTINCT (m.facility_id, {_day_index()}))",
            start,
            at,
        )
        alive = _scalar(
            conn,
            f"WITH unit AS ({_ALIVE}) "
            "SELECT count(*) FROM unit, generate_series(0, :days - 1) AS k "
            "WHERE unit.born < :at - k * interval '1 day'",
            {"at": at, "days": days},
        )
        if not alive:
            return math.nan
        return int(events or 0) / int(alive)

    return _per_run(conn, f"net_{days}", at, compute)


def _same_dates(day: date, first_year: int) -> list[date]:
    """Даты прошлых лет с днём года не дальше `SAME_DATE_DAYS` от `day`.

    Расстояние по кругу в 365 дней, как в `panel.py`: 30 декабря и 2 января
    одного года стоят рядом.
    """
    target = day.timetuple().tm_yday
    dates = []
    for year in range(first_year, day.year):
        cursor = date(year, 1, 1)
        while cursor.year == year:
            gap = abs(cursor.timetuple().tm_yday - target)
            if min(gap, 365 - gap) <= SAME_DATE_DAYS:
                dates.append(cursor)
            cursor += timedelta(days=1)
    return dates


def _date_event_share_prev_years(conn: Connection, at: datetime) -> float:
    """Доля пар «участок и сутки» с событием в эту же дату прошлых лет."""

    def compute() -> float:
        day = _local_date(at)
        first = _scalar(
            conn, "SELECT min(occurred_at) FROM alarm_event WHERE occurred_at < :at", {"at": at}
        )
        if first is None:
            return math.nan
        dates = _same_dates(day, _local_date(first).year)
        if not dates:
            return math.nan
        # Точки Москвы переводятся в UTC для границ отрезка.
        start = datetime.combine(dates[0], time(), UTC) - MOSCOW
        stop = datetime.combine(date(day.year, 1, 1), time(), UTC) - MOSCOW
        local = "(m.ts AT TIME ZONE 'Europe/Moscow')::date"
        events = _query(
            conn,
            _NETWORK,
            "event_moment",
            f"count(DISTINCT (m.facility_id, {local})) FILTER (WHERE {local} = ANY(:dates))",
            start,
            min(stop, at),
            dates=dates,
        )
        alive = _scalar(
            conn,
            f"WITH unit AS ({_ALIVE}) "
            "SELECT count(*) FROM unit, unnest(CAST(:dates AS date[])) AS d "
            "WHERE (unit.born AT TIME ZONE 'Europe/Moscow')::date <= d",
            {"dates": dates, "at": at},
        )
        if not alive:
            return math.nan
        return int(events or 0) / int(alive)

    return _per_run(conn, "date_share", at, compute)


# --- Календарь ---------------------------------------------------------


def _calendar(read: Callable[[date], float]) -> registry.Builder:
    return lambda _c, _f, at: read(_local_date(at))


BUILDERS: dict[str, registry.Builder] = {
    # участок: все тревоги доступа
    "n_alarms_7d": lambda c, f, a: _count(c, f, a, 7, "access_moment"),
    "alarm_day_share_7d": lambda c, f, a: _day_share(c, f, a, 7, "access_moment"),
    "alarm_day_share_365d": lambda c, f, a: _day_share(c, f, a, 365, "access_moment"),
    "days_since_last_alarm": lambda c, f, a: _days_since(c, f, a, "access_moment"),
    "hours_since_last_alarm": lambda c, f, a: _hours_since(c, f, a, "access_moment"),
    # участок: события
    "n_armed_7d": lambda c, f, a: _count(c, f, a, 7, "event_moment"),
    "n_armed_90d": lambda c, f, a: _count(c, f, a, 90, "event_moment"),
    "armed_day_share_30d": lambda c, f, a: _day_share(c, f, a, 30, "event_moment"),
    "days_since_last_armed": lambda c, f, a: _days_since(c, f, a, "event_moment"),
    "hours_since_last_armed": lambda c, f, a: _hours_since(c, f, a, "event_moment"),
    "armed_same_weekday_1w": _event_week_ago,
    # участок: устройство
    "n_channels": _n_channels,
    "unit_age_days": _age_days,
    # режим охраны
    "disarm_hours_7d": lambda c, f, a: _disarm_hours(c, f, a, 7),
    "disarm_share_30d": lambda c, f, a: _disarm_hours(c, f, a, 30) / (30 * 24.0),
    "hours_since_guard_change": _hours_since_guard_change,
    # объект
    "obj_alarms_1d": lambda c, f, a: _object_alarms(c, f, a, 1),
    "obj_alarms_7d": lambda c, f, a: _object_alarms(c, f, a, 7),
    "obj_units_alarmed_7d": lambda c, f, a: _object_units_alarmed(c, f, a, 7),
    # сеть
    "net_event_share_1d": lambda c, _f, a: _net_event_share(c, a, 1),
    "net_event_share_7d": lambda c, _f, a: _net_event_share(c, a, 7),
    "date_event_share_prev_years": lambda c, _f, a: _date_event_share_prev_years(c, a),
    # календарь прогнозируемых суток; день недели с понедельника, как в `data.py`
    "day_of_week": _calendar(lambda d: d.weekday()),
    "day_of_month": _calendar(lambda d: d.day),
    "day_of_year": _calendar(lambda d: d.timetuple().tm_yday),
    "week_of_year": _calendar(lambda d: d.isocalendar()[1]),
    "is_day_off": _calendar(lambda d: int(is_day_off(d))),
    "day_off_chain": _calendar(day_off_chain),
}
