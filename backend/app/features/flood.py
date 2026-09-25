"""Признаки направления «риск подтопления».

Имена совпадают с `ml/flood/04_panel.py` буква в букву: по ним модель берёт
столбцы. Разбор признаков и чисел лежит в ADR 0008 и ADR 0009.

## Точка расчёта

Обучение ставит точку расчёта в начало часа, и все окна кончаются строго на
ней. Сервис считает в любой момент, поэтому момент `at` округляется вниз до
начала часа. Правило одно на весь модуль: `occurred_at < начало часа`. Тест
`tests/test_features_no_leak.py` проверяет его для каждого признака реестра.

## Сопоставление со схемой

Выгрузка знает объект, галерею, пикет и комплекс. Схема `backend` пока знает
`facility`, `collector` и `district` (спецификация бэкенда §6, пометка
«Сейчас»). До перехода на дерево объектов соседство читается так:

- единица — строка `facility`;
- объект — все `facility` того же `collector`;
- комплекс — все `facility` того же `district`.

Насос без пикета в схеме отдельной строки не имеет: его события лежат на
какой-то `facility` объекта и видны соседям так же, как в обучении.

## Вода и плановые проверки

Сигнал «Затоплен» или «Не замкнут» внутри рабочего окна (будни, кроме
праздников, с 8:00 до 16:00 по Москве) оставляют в основном плановые проверки.
Водой он не считается ни в признаках событий, ни в метке (ADR 0010). Условие
держит функция `water_sql`, одна на модуль и на плагин.

## Коды событий

Журнал СМВУ пишет тексты, схема `backend` держит коды. Соответствие:

| Код | Значение журнала | Тип канала |
|---|---|---|
| `FLOODED` | «Затоплен» | Состояние насоса |
| `FLOOD_SENSOR_OPEN` | «Не замкнут» | Датчик затопления |
| `PUMP_ON`, `PUMP_OFF` | «Включен», «Выключен» | Состояние насоса |
| `PUMP_ALL_RUNNING` | «Работают все насосы в АНС» | Состояние насоса |
| `PUMP_UNAVAILABLE` | «Обесточен», «Неисправен», «Отключено устройство» | Состояние насоса |
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features import registry

DIRECTION = "FLOOD_RISK"

FLOODED = "FLOODED"
FLOOD_SENSOR_OPEN = "FLOOD_SENSOR_OPEN"
PUMP_ON = "PUMP_ON"
PUMP_OFF = "PUMP_OFF"
PUMP_ALL_RUNNING = "PUMP_ALL_RUNNING"
PUMP_UNAVAILABLE = "PUMP_UNAVAILABLE"

LABEL_TYPES = (FLOODED, FLOOD_SENSOR_OPEN)
STATE_TYPES = (PUMP_ON, PUMP_OFF)
ALL_TYPES = (*LABEL_TYPES, *STATE_TYPES, PUMP_ALL_RUNNING, PUMP_UNAVAILABLE)

# Типы каналов в таблице `sensor`.
SENSOR_PUMP = "PUMP"
SENSOR_FLOOD = "FLOOD_SENSOR"

# Порог мигания из обучения: квантиль 0,99 смен за час у исправных насосов на
# обучающем отрезке (`ml/flood/out/panel_stats.json`, ADR 0009).
BLINK_CHANGES_PER_HOUR = 14.0
# Рабочее окно плановых проверок и часовой пояс журнала СМВУ (ADR 0010).
WORK_FROM, WORK_TO = 8, 16
TIMEZONE = "Europe/Moscow"
# Интервал «насос включён» длиннее суток обрезается: за ним пропуск данных.
MAX_ON_HOURS = 24


def holidays() -> list[str]:
    """Постоянные праздники в виде `MM-DD`: в праздник рабочего окна нет."""
    from app.features.calendar_ru import FIXED_HOLIDAYS

    return sorted(f"{month:02d}-{day:02d}" for month, day in FIXED_HOLIDAYS)


def water_sql(alias: str = "e") -> str:
    """Условие «сигнал затопления вне рабочего окна». Нужен параметр `:holidays`."""
    local = f"({alias}.occurred_at AT TIME ZONE '{TIMEZONE}')"
    return (
        f"NOT (extract(isodow FROM {local}) <= 5 "
        f"AND extract(hour FROM {local}) >= {WORK_FROM} "
        f"AND extract(hour FROM {local}) < {WORK_TO} "
        f"AND to_char({local}, 'MM-DD') <> ALL(:holidays))"
    )


WATER = water_sql("e")


def hour_of(at: datetime) -> datetime:
    """Начало часа расчёта. Все окна кончаются строго на нём."""
    return at.replace(minute=0, second=0, microsecond=0)


def _scope(level: str) -> str:
    """Условие на `facility_id` для единицы, объекта или комплекса."""
    if level == "unit":
        return "e.facility_id = :facility_id"
    column = {"object": "collector", "complex": "district"}[level]
    return (
        f"e.facility_id IN (SELECT id FROM facility WHERE {column} = "
        f"(SELECT {column} FROM facility WHERE id = :facility_id))"
    )


def _counts(conn: Connection, facility_id: str, at: datetime, level: str) -> dict[str, float]:
    """Счётчики событий по окнам для единицы, объекта или комплекса."""
    h = hour_of(at)
    row = (
        conn.execute(
            text(
                f"""
            SELECT
                {
                    ", ".join(
                        f"count(DISTINCT date_trunc('hour', e.occurred_at)) FILTER ("
                        f"WHERE e.alarm_type = ANY(:label) AND {WATER} AND e.occurred_at >= :h - "
                        f"interval '{w} hours') AS evt_hours_{w}h"
                        for w in (24, 168, 720, 2160, 8760)
                    )
                },
                count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER (
                    WHERE e.alarm_type = ANY(:label) AND {WATER}
                      AND e.occurred_at >= :h - interval '24 hours') AS m_evt_24h,
                count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER (
                    WHERE e.alarm_type = ANY(:label) AND {WATER}
                      AND e.occurred_at >= :h - interval '168 hours') AS m_evt_168h,
                {
                    ", ".join(
                        f"count(*) FILTER (WHERE e.alarm_type = ANY(:state) "
                        f"AND e.occurred_at >= :h - interval '{w} hours') AS chg_{w}h"
                        for w in (1, 6, 24)
                    )
                },
                {
                    ", ".join(
                        f"count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER ("
                        f"WHERE e.alarm_type = :unavail AND e.occurred_at >= :h - "
                        f"interval '{w} hours') AS unavail_{w}h"
                        for w in (24, 168)
                    )
                },
                {
                    ", ".join(
                        f"count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER ("
                        f"WHERE e.alarm_type = :allp AND e.occurred_at >= :h - "
                        f"interval '{w} hours') AS allp_{w}h"
                        for w in (24, 168)
                    )
                }
            FROM alarm_event e
            WHERE {_scope(level)}
              AND e.alarm_type = ANY(:types)
              AND e.occurred_at >= :h - interval '8760 hours' AND e.occurred_at < :h
            """
            ),
            {
                "facility_id": facility_id,
                "h": h,
                "label": list(LABEL_TYPES),
                "holidays": holidays(),
                "state": list(STATE_TYPES),
                "unavail": PUMP_UNAVAILABLE,
                "allp": PUMP_ALL_RUNNING,
                "types": list(ALL_TYPES),
            },
        )
        .mappings()
        .one()
    )
    return {key: float(value or 0) for key, value in row.items()}


def _blink(conn: Connection, facility_id: str, at: datetime, level: str) -> dict[str, float]:
    """Часы насосов, в которые смен состояния было больше порога мигания."""
    h = hour_of(at)
    row = (
        conn.execute(
            text(
                f"""
            SELECT count(*) FILTER (WHERE hour >= :h - interval '24 hours') AS blink_24h,
                   count(*) AS blink_168h
            FROM (
                SELECT e.sensor_id, date_trunc('hour', e.occurred_at) AS hour
                FROM alarm_event e
                WHERE {_scope(level)} AND e.alarm_type = ANY(:state)
                  AND e.occurred_at >= :h - interval '168 hours' AND e.occurred_at < :h
                GROUP BY 1, 2
                HAVING count(*) > :blink
            ) hours
            """
            ),
            {
                "facility_id": facility_id,
                "h": h,
                "state": list(STATE_TYPES),
                "blink": BLINK_CHANGES_PER_HOUR,
            },
        )
        .mappings()
        .one()
    )
    return {key: float(value or 0) for key, value in row.items()}


def _on_minutes(conn: Connection, facility_id: str, at: datetime, level: str) -> dict[str, float]:
    """Минуты во включённом состоянии по окнам.

    Насос включён от `PUMP_ON` до следующей смены того же канала, но не дольше
    суток и не дальше начала часа расчёта. Следующая смена ищется только среди
    записей до начала часа: событие после него признак видеть не имеет права.
    """
    h = hour_of(at)
    windows = (24, 168, 720)
    parts = ", ".join(
        f"""coalesce(sum(greatest(extract(epoch FROM (
                least(b, :h) - greatest(a, :h - interval '{w} hours'))), 0)) / 60.0, 0)
            AS on_min_{w}h"""
        for w in windows
    )
    row = (
        conn.execute(
            text(
                f"""
            WITH s AS (
                SELECT e.sensor_id, e.occurred_at AS a, e.alarm_type,
                       lead(e.occurred_at) OVER (
                           PARTITION BY e.sensor_id ORDER BY e.occurred_at, e.alarm_type
                       ) AS nxt
                FROM alarm_event e
                WHERE {_scope(level)} AND e.alarm_type = ANY(:state)
                  AND e.occurred_at >= :h - interval '{max(windows) + MAX_ON_HOURS} hours'
                  AND e.occurred_at < :h
            ),
            iv AS (
                SELECT a, least(coalesce(nxt, a + interval '{MAX_ON_HOURS} hours'),
                                a + interval '{MAX_ON_HOURS} hours') AS b
                FROM s WHERE alarm_type = :on
            )
            SELECT {parts} FROM iv
            """
            ),
            {"facility_id": facility_id, "h": h, "state": list(STATE_TYPES), "on": PUMP_ON},
        )
        .mappings()
        .one()
    )
    return {key: float(value or 0) for key, value in row.items()}


def _unit_sensors(conn: Connection, facility_id: str, at: datetime) -> dict[str, float]:
    """Насосы и датчики затопления единицы.

    Первый источник — таблица `sensor`. Когда в ней нет строк единицы, число
    выводится из журнала: каналы, которые хоть раз подали сигнал до `at`.
    """
    row = conn.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE sensor_type = :pump),
                   count(*) FILTER (WHERE sensor_type = :flood)
            FROM sensor
            WHERE facility_id = :facility_id
              AND (installed_at IS NULL OR installed_at <= :at)
            """
        ),
        {"facility_id": facility_id, "pump": SENSOR_PUMP, "flood": SENSOR_FLOOD, "at": at},
    ).one()
    pumps, sensors = int(row[0]), int(row[1])
    if pumps == 0 and sensors == 0:
        fallback = conn.execute(
            text(
                """
                SELECT count(DISTINCT sensor_id) FILTER (WHERE alarm_type <> :sensor_open),
                       count(DISTINCT sensor_id) FILTER (WHERE alarm_type = :sensor_open)
                FROM alarm_event
                WHERE facility_id = :facility_id AND alarm_type = ANY(:types)
                  AND occurred_at < :h
                """
            ),
            {
                "facility_id": facility_id,
                "sensor_open": FLOOD_SENSOR_OPEN,
                "types": list(ALL_TYPES),
                "h": hour_of(at),
            },
        ).one()
        pumps, sensors = int(fallback[0]), int(fallback[1])
    return {"pumps": float(pumps), "flood_sensors": float(sensors)}


def _first_seen(conn: Connection, facility_id: str, at: datetime) -> datetime:
    """Первая запись единицы до начала часа. Нет записей значит сам час."""
    h = hour_of(at)
    row = conn.execute(
        text(
            "SELECT min(occurred_at) FROM alarm_event "
            "WHERE facility_id = :facility_id AND occurred_at < :h"
        ),
        {"facility_id": facility_id, "h": h},
    ).fetchone()
    first = row[0] if row and row[0] is not None else h
    return hour_of(first)


def _hours_since_last(conn: Connection, facility_id: str, at: datetime) -> float:
    """Часов от последнего события до начала часа расчёта.

    Событием считается только вода, сигнал вне рабочего окна (ADR 0010).
    Событий не было значит часов от первой записи единицы плюс один, как в
    обучении: там отсчёт шёл от начала жизни единицы.
    """
    h = hour_of(at)
    row = conn.execute(
        text(
            "SELECT max(e.occurred_at) FROM alarm_event e "
            "WHERE e.facility_id = :facility_id AND e.alarm_type = ANY(:label) "
            f"AND {WATER} AND e.occurred_at < :h"
        ),
        {
            "facility_id": facility_id,
            "label": list(LABEL_TYPES),
            "holidays": holidays(),
            "h": h,
        },
    ).fetchone()
    if row and row[0] is not None:
        return (h - hour_of(row[0])).total_seconds() // 3600
    return (h - _first_seen(conn, facility_id, at)).total_seconds() // 3600 + 1


def _pump_share(conn: Connection, facility_id: str, at: datetime, hours: int) -> float:
    minutes = _on_minutes(conn, facility_id, at, "unit")[f"on_min_{hours}h"]
    pumps = max(_unit_sensors(conn, facility_id, at)["pumps"], 1.0)
    return minutes / (60.0 * hours * pumps)


def _pump_growth(conn: Connection, facility_id: str, at: datetime) -> float:
    on = _on_minutes(conn, facility_id, at, "unit")
    return (on["on_min_24h"] / 24.0) / max(on["on_min_720h"] / 720.0, 0.1)


def _neighbour(source: Any, key: str, outer: str, inner: str) -> registry.Builder:
    """Признак соседей: сумма по внешнему уровню минус сумма по внутреннему."""

    def build(conn: Connection, facility_id: str, at: datetime) -> float:
        return source(conn, facility_id, at, outer)[key] - source(conn, facility_id, at, inner)[key]

    return build


def _cal(at: datetime) -> dict[str, float]:
    from app.features.calendar_ru import day_off_chain, is_day_off, is_holiday

    day = hour_of(at).date()
    return {
        "hour": float(hour_of(at).hour),
        "season": float((day.month % 12) // 3),
        "month": float(day.month),
        "day_of_year": float(day.timetuple().tm_yday),
        # Понедельник это ноль, как в `ml/access/calendar_ru.py::build_calendar`.
        "day_of_week": float(day.weekday()),
        "is_day_off": float(is_day_off(day)),
        "is_holiday": float(is_holiday(day)),
        "day_off_chain": float(day_off_chain(day)),
    }


def _unit(key: str) -> registry.Builder:
    return lambda c, f, a: _counts(c, f, a, "unit")[key]


BUILDERS: dict[str, registry.Builder] = {
    # 1. насосы единицы
    "pump_changes_1h": _unit("chg_1h"),
    "pump_changes_6h": _unit("chg_6h"),
    "pump_changes_24h": _unit("chg_24h"),
    "pump_blink_hours_24h": lambda c, f, a: _blink(c, f, a, "unit")["blink_24h"],
    "pump_blink_hours_168h": lambda c, f, a: _blink(c, f, a, "unit")["blink_168h"],
    "pump_on_share_24h": lambda c, f, a: _pump_share(c, f, a, 24),
    "pump_on_share_168h": lambda c, f, a: _pump_share(c, f, a, 168),
    "pump_on_growth_24_720": _pump_growth,
    "pump_unavailable_24h": _unit("unavail_24h"),
    "pump_unavailable_168h": _unit("unavail_168h"),
    "pump_all_running_24h": _unit("allp_24h"),
    "pump_all_running_168h": _unit("allp_168h"),
    # 2. история событий единицы
    "evt_hours_24h": _unit("evt_hours_24h"),
    "evt_hours_168h": _unit("evt_hours_168h"),
    "evt_hours_720h": _unit("evt_hours_720h"),
    "evt_hours_2160h": _unit("evt_hours_2160h"),
    "evt_hours_8760h": _unit("evt_hours_8760h"),
    "evt_moments_168h": _unit("m_evt_168h"),
    "evt_hours_since_last": _hours_since_last,
    # 3. соседи: объект без единицы, комплекс без своего объекта
    "obj_events_24h": _neighbour(_counts, "m_evt_24h", "object", "unit"),
    "obj_events_168h": _neighbour(_counts, "m_evt_168h", "object", "unit"),
    "obj_pump_changes_24h": _neighbour(_counts, "chg_24h", "object", "unit"),
    "obj_pump_blink_24h": _neighbour(_blink, "blink_24h", "object", "unit"),
    "obj_pump_unavailable_24h": _neighbour(_counts, "unavail_24h", "object", "unit"),
    "obj_pump_all_running_24h": _neighbour(_counts, "allp_24h", "object", "unit"),
    "obj_pump_on_minutes_24h": _neighbour(_on_minutes, "on_min_24h", "object", "unit"),
    "cx_events_24h": _neighbour(_counts, "m_evt_24h", "complex", "object"),
    "cx_events_168h": _neighbour(_counts, "m_evt_168h", "complex", "object"),
    "cx_pump_blink_24h": _neighbour(_blink, "blink_24h", "complex", "object"),
    "cx_pump_unavailable_24h": _neighbour(_counts, "unavail_24h", "complex", "object"),
    "cx_pump_all_running_168h": _neighbour(_counts, "allp_168h", "complex", "object"),
    # устройство места
    "unit_pumps": lambda c, f, a: _unit_sensors(c, f, a)["pumps"],
    "unit_flood_sensors": lambda c, f, a: _unit_sensors(c, f, a)["flood_sensors"],
    "unit_age_days": lambda c, f, a: float(
        (hour_of(a) - _first_seen(c, f, a)) // timedelta(days=1)
    ),
    # 4. календарь
    "cal_hour": lambda _c, _f, a: _cal(a)["hour"],
    "cal_season": lambda _c, _f, a: _cal(a)["season"],
    "cal_month": lambda _c, _f, a: _cal(a)["month"],
    "cal_day_of_year": lambda _c, _f, a: _cal(a)["day_of_year"],
    "cal_day_of_week": lambda _c, _f, a: _cal(a)["day_of_week"],
    "cal_is_day_off": lambda _c, _f, a: _cal(a)["is_day_off"],
    "cal_is_holiday": lambda _c, _f, a: _cal(a)["is_holiday"],
    "cal_day_off_chain": lambda _c, _f, a: _cal(a)["day_off_chain"],
}
