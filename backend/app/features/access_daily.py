"""Суточные признаки направления «несанкционированный доступ».

Имена совпадают с `ml/access/07_daily_panel.py` буква в букву: по ним модель
берёт столбцы. Расхождение в имени модель не заметит, а прогноз станет мусором.

Точка расчёта стоит в начале суток, и все окна кончаются строго на ней. Правило
одно на весь модуль: `occurred_at < :at`. Тест `tests/test_features_no_leak.py`
проверяет его для каждого признака реестра.

Суточные окна строятся поверх той же таблицы `alarm_event`, что и часовые.
Отдельного хранилища не нужно: сутки это просто окно в 24 часа.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features import registry
from app.features.access import (
    ACCESS_ALARM_TYPES,
    SECURITY_ARMED,
    SECURITY_DISARMED,
    _facility_first_seen,
)

DIRECTION = "UNAUTHORIZED_ACCESS"
DAY = 24


def _count(
    conn: Connection, facility_id: str, at: datetime, days: int, armed_only: bool
) -> int:
    """Число тревог доступа за окно в сутках.

    `armed_only` оставляет только тревоги вне окна снятия охраны: метка модели
    считает событием именно их (ADR 0001).
    """
    row = conn.execute(
        text(
            "SELECT count(*) FROM alarm_event e "
            "WHERE e.facility_id = :facility_id AND e.alarm_type = ANY(:types) "
            "AND e.occurred_at >= :start AND e.occurred_at < :at"
            + (" AND " + _armed_predicate() if armed_only else "")
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=days * DAY),
            "at": at,
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _armed_predicate() -> str:
    """Тревога считается «на охране», если последнее переключение режима до неё
    было постановкой, либо переключений не было вовсе."""
    return (
        "coalesce((SELECT s.alarm_type FROM alarm_event s "
        " WHERE s.facility_id = e.facility_id AND s.occurred_at <= e.occurred_at "
        "   AND s.alarm_type IN (:armed, :disarmed) "
        " ORDER BY s.occurred_at DESC LIMIT 1), :armed) = :armed"
    )


def _days_with(
    conn: Connection, facility_id: str, at: datetime, days: int, armed_only: bool
) -> int:
    """Число различных суток с тревогой внутри окна."""
    row = conn.execute(
        text(
            "SELECT count(DISTINCT date_trunc('day', e.occurred_at)) FROM alarm_event e "
            "WHERE e.facility_id = :facility_id AND e.alarm_type = ANY(:types) "
            "AND e.occurred_at >= :start AND e.occurred_at < :at"
            + (" AND " + _armed_predicate() if armed_only else "")
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=days * DAY),
            "at": at,
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _days_since(
    conn: Connection, facility_id: str, at: datetime, armed_only: bool
) -> int:
    row = conn.execute(
        text(
            "SELECT max(e.occurred_at) FROM alarm_event e "
            "WHERE e.facility_id = :facility_id AND e.alarm_type = ANY(:types) "
            "AND e.occurred_at < :at" + (" AND " + _armed_predicate() if armed_only else "")
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "at": at,
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
        },
    ).fetchone()
    last = row[0] if row and row[0] is not None else _facility_first_seen(conn, facility_id, at)
    return max(int((at - last).total_seconds() // (DAY * 3600)), 0)


def _disarm_hours(conn: Connection, facility_id: str, at: datetime, days: int) -> float:
    """Часы, которые объект простоял снятым с охраны внутри окна.

    Окно снятия это отрезок от переключения в `SECURITY_DISARMED` до следующей
    постановки. Незакрытый отрезок обрезается точкой расчёта.
    """
    row = conn.execute(
        text(
            """
            WITH switch AS (
                SELECT occurred_at AS ts, alarm_type,
                       lead(occurred_at) OVER (ORDER BY occurred_at) AS next_ts
                FROM alarm_event
                WHERE facility_id = :facility_id
                  AND alarm_type IN (:armed, :disarmed)
                  AND occurred_at < :at
            )
            SELECT coalesce(sum(extract(epoch FROM (
                least(coalesce(next_ts, :at), :at) - greatest(ts, :start)
            ))) / 3600.0, 0)
            FROM switch
            WHERE alarm_type = :disarmed
              AND least(coalesce(next_ts, :at), :at) > greatest(ts, :start)
            """
        ),
        {
            "facility_id": facility_id,
            "at": at,
            "start": at - timedelta(hours=days * DAY),
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
        },
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _object_alarms(conn: Connection, facility_id: str, at: datetime, days: int) -> int:
    """Тревоги соседей по коллектору. Отвечает на вопрос «шумит ли участок»."""
    row = conn.execute(
        text(
            "SELECT count(*) FROM alarm_event e "
            "JOIN facility f ON f.id = e.facility_id "
            "WHERE f.collector = (SELECT collector FROM facility WHERE id = :facility_id) "
            "AND e.alarm_type = ANY(:types) "
            "AND e.occurred_at >= :start AND e.occurred_at < :at"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=days * DAY),
            "at": at,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _object_units_alarmed(conn: Connection, facility_id: str, at: datetime, days: int) -> int:
    row = conn.execute(
        text(
            "SELECT count(DISTINCT e.facility_id) FROM alarm_event e "
            "JOIN facility f ON f.id = e.facility_id "
            "WHERE f.collector = (SELECT collector FROM facility WHERE id = :facility_id) "
            "AND e.alarm_type = ANY(:types) "
            "AND e.occurred_at >= :start AND e.occurred_at < :at"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=days * DAY),
            "at": at,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _armed_same_weekday_1w(conn: Connection, facility_id: str, at: datetime) -> int:
    """Была ли тревога вне охраны ровно неделю назад. Недельная повторяемость."""
    start = at - timedelta(days=7)
    row = conn.execute(
        text(
            "SELECT 1 FROM alarm_event e "
            "WHERE e.facility_id = :facility_id AND e.alarm_type = ANY(:types) "
            "AND e.occurred_at >= :start AND e.occurred_at < :stop "
            "AND " + _armed_predicate() + " LIMIT 1"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": start,
            "stop": start + timedelta(days=1),
            "at": at,
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
        },
    ).fetchone()
    return 1 if row else 0


def _n_channels(conn: Connection, facility_id: str, at: datetime) -> int:
    """Сколько каналов доступа известно на объекте. Свойство места, не события.

    Считается по всей истории до `at`: справочника оборудования в схеме нет,
    и число каналов выводится из того, что хоть раз подавало сигнал.
    """
    row = conn.execute(
        text(
            "SELECT count(DISTINCT sensor_id) FROM alarm_event "
            "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) "
            "AND occurred_at < :at"
        ),
        {"facility_id": facility_id, "types": list(ACCESS_ALARM_TYPES), "at": at},
    ).fetchone()
    return int(row[0]) if row else 0


def _day_off_chain(_conn: Connection, _facility_id: str, at: datetime) -> int:
    """Длина цепочки нерабочих дней, в которую попадает `at`. Ноль в будни."""
    from app.features.calendar_ru import day_off_chain

    return day_off_chain(at.date())


def _is_day_off(_conn: Connection, _facility_id: str, at: datetime) -> int:
    from app.features.calendar_ru import is_day_off

    return int(is_day_off(at.date()))


BUILDERS: dict[str, registry.Builder] = {
    "days_since_last_armed": lambda c, f, a: _days_since(c, f, a, armed_only=True),
    "days_since_last_alarm": lambda c, f, a: _days_since(c, f, a, armed_only=False),
    "armed_day_share_30d": lambda c, f, a: _days_with(c, f, a, 30, True) / 30.0,
    "alarm_day_share_7d": lambda c, f, a: _days_with(c, f, a, 7, False) / 7.0,
    "alarm_day_share_365d": lambda c, f, a: _days_with(c, f, a, 365, False) / 365.0,
    "n_alarms_7d": lambda c, f, a: _count(c, f, a, 7, False),
    "n_armed_90d": lambda c, f, a: _count(c, f, a, 90, True),
    "obj_alarms_1d": lambda c, f, a: _object_alarms(c, f, a, 1),
    "obj_alarms_7d": lambda c, f, a: _object_alarms(c, f, a, 7),
    "obj_units_alarmed_7d": lambda c, f, a: _object_units_alarmed(c, f, a, 7),
    "disarm_hours_7d": lambda c, f, a: _disarm_hours(c, f, a, 7),
    "disarm_share_30d": lambda c, f, a: _disarm_hours(c, f, a, 30) / (30 * 24.0),
    "armed_same_weekday_1w": _armed_same_weekday_1w,
    "n_channels": _n_channels,
    "day_of_year": lambda _c, _f, a: a.timetuple().tm_yday,
    "day_of_month": lambda _c, _f, a: a.day,
    "week_of_year": lambda _c, _f, a: a.isocalendar().week,
    "day_of_week": lambda _c, _f, a: (a.weekday() + 1) % 7,
    "is_day_off": _is_day_off,
    "day_off_chain": _day_off_chain,
}
