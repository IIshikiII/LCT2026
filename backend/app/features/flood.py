"""Признаки направления «риск подтопления».

Имена совпадают с `ml/flood/04_panel.py` буква в букву: по ним модель берёт
столбцы. Разбор признаков и чисел лежит в ADR 0008, 0009, 0012 и 0013.

## Точка расчёта

Модель ADR 0012 училась на суточной сетке: точка расчёта стоит в полночь по
Москве, все окна кончаются строго на ней. Сервис считает в любой момент,
поэтому момент `at` сдвигается назад к последней московской полуночи
(`point_of`). Правило одно на весь модуль: `occurred_at < точка расчёта`. Тест
`tests/test_features_flood_no_leak.py` проверяет его для каждого признака
реестра.

Прогноз, посчитанный днём, отвечает на тот же вопрос, что утренний: будет ли
вода на пикете в текущие московские сутки. Сигналы этих суток признаки не
видят: разметка «вода или проверка» есть только у законченных суток.

## Вода и плановые проверки

Сигнал «Затоплен» или «Не замкнут» оставляет и вода, и плановая проверка.
Сутки пикета с сигналом размечает классификатор ADR 0012
(`app/features/flood_water.py`), итог лежит в таблице `flood_water_day`.
Признаки `evt_*`, `obj_events_*`, `cx_events_*`, `recent_evt_*` читают только
сутки с водой, признаки `check_*` и `recent_check_*` только сутки проверки.
Сутки без разметки не идут ни туда, ни туда.

## Сопоставление со схемой

Выгрузка знает объект, галерею, пикет и комплекс. Схема `backend` пока знает
`facility`, `collector` и `district` (спецификация бэкенда §6, пометка
«Сейчас»). До перехода на дерево объектов соседство читается так:

- единица — строка `facility`;
- объект — все `facility` того же `collector`;
- комплекс — все `facility` того же `district`.

## Погода

Модель читает погоду Москвы одним рядом (`ml/flood/08_weather.py`). В
`weather_hourly` этот ряд лежит под районом `MOSCOW`. Нет строк значит суммы
равны нулю, а температура пропущена: бустер ведёт пропуск по своей ветке, как
в первые часы обучающей выборки.

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

import functools
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta, timezone
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
# Часовой пояс журнала СМВУ. В SQL пояс называется по имени, в Python это
# постоянный сдвиг: Москва не переводит часы с 2014 года, выгрузка начата в 2019.
TIMEZONE = "Europe/Moscow"
MOSCOW = timezone(timedelta(hours=3))
# Район, под которым `weather_hourly` держит ряд погоды Москвы.
WEATHER_AREA = "MOSCOW"
# Интервал «насос включён» длиннее суток обрезается: за ним пропуск данных.
MAX_ON_HOURS = 24


def point_of(at: datetime) -> datetime:
    """Точка расчёта: последняя полночь по Москве не позже `at`, в UTC."""
    moment = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    local = moment.astimezone(MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def local_day(at: datetime) -> date:
    """Московские сутки момента `at`."""
    moment = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    return moment.astimezone(MOSCOW).date()


def day_sql(alias: str = "e") -> str:
    """Московские сутки события в SQL."""
    return f"({alias}.occurred_at AT TIME ZONE '{TIMEZONE}')::date"


def water_sql(alias: str = "e", water: bool = True) -> str:
    """Условие «сигнал лежит в сутках с водой» или «в сутках проверки»."""
    verdict = "w.is_water" if water else "NOT w.is_water"
    return (
        f"EXISTS (SELECT 1 FROM flood_water_day w WHERE w.facility_id = {alias}.facility_id "
        f"AND w.day = {day_sql(alias)} AND {verdict})"
    )


WATER = water_sql("e", water=True)
CHECK = water_sql("e", water=False)

# Кэш запросов на один вектор признаков. Один запрос `_counts` кормит два
# десятка признаков, и без кэша он повторялся бы на каждом. Кэш живёт только
# внутри `memo()`: вне его каждый вызов идёт в базу, и тест на утечку видит
# свежие данные.
_MEMO: ContextVar[dict[tuple[Any, ...], Any] | None] = ContextVar("flood_memo", default=None)


@contextmanager
def memo() -> Iterator[None]:
    """Включает кэш запросов на время расчёта одного вектора."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def _cached[T](fn: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(conn: Connection, *args: Any) -> T:
        store = _MEMO.get()
        if store is None:
            return fn(conn, *args)
        key = (fn.__name__, *args)
        if key not in store:
            store[key] = fn(conn, *args)
        return store[key]  # type: ignore[no-any-return]

    return wrapper


EVT_WINDOWS = (6, 12, 24, 168, 720, 2160, 8760)
CHECK_WINDOWS = (24, 168, 720)
ON_WINDOWS = (6, 24, 168, 720)


def _scope(level: str) -> str:
    """Условие на `facility_id` для единицы, объекта или комплекса."""
    if level == "unit":
        return "e.facility_id = :facility_id"
    column = {"object": "collector", "complex": "district"}[level]
    return (
        f"e.facility_id IN (SELECT id FROM facility WHERE {column} = "
        f"(SELECT {column} FROM facility WHERE id = :facility_id))"
    )


def _hours(name: str, condition: str, window: int) -> str:
    return (
        f"count(DISTINCT date_trunc('hour', e.occurred_at)) FILTER ("
        f"WHERE e.alarm_type = ANY(:label) AND {condition} "
        f"AND e.occurred_at >= :p - interval '{window} hours') AS {name}_{window}h"
    )


def _moments(name: str, condition: str, window: int) -> str:
    return (
        f"count(DISTINCT (e.sensor_id, e.occurred_at)) FILTER ("
        f"WHERE {condition} AND e.occurred_at >= :p - interval '{window} hours') "
        f"AS {name}_{window}h"
    )


@_cached
def _counts(conn: Connection, facility_id: str, at: datetime, level: str) -> dict[str, float]:
    """Счётчики событий по окнам для единицы, объекта или комплекса."""
    parts = [
        *(_hours("evt_hours", WATER, w) for w in EVT_WINDOWS),
        *(_hours("check_hours", CHECK, w) for w in CHECK_WINDOWS),
        *(_moments("m_evt", f"e.alarm_type = ANY(:label) AND {WATER}", w) for w in (24, 168)),
        *(
            f"count(*) FILTER (WHERE e.alarm_type = ANY(:state) "
            f"AND e.occurred_at >= :p - interval '{w} hours') AS chg_{w}h"
            for w in (1, 6, 24)
        ),
        *(_moments("unavail", "e.alarm_type = :unavail", w) for w in (24, 168)),
        *(_moments("allp", "e.alarm_type = :allp", w) for w in (24, 168)),
    ]
    row = (
        conn.execute(
            text(
                f"""
            SELECT {", ".join(parts)}
            FROM alarm_event e
            WHERE {_scope(level)}
              AND e.alarm_type = ANY(:types)
              AND e.occurred_at >= :p - interval '8760 hours' AND e.occurred_at < :p
            """
            ),
            {
                "facility_id": facility_id,
                "p": point_of(at),
                "label": list(LABEL_TYPES),
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


@_cached
def _blink(conn: Connection, facility_id: str, at: datetime, level: str) -> dict[str, float]:
    """Часы насосов, в которые смен состояния было больше порога мигания."""
    row = (
        conn.execute(
            text(
                f"""
            SELECT count(*) FILTER (WHERE hour >= :p - interval '24 hours') AS blink_24h,
                   count(*) AS blink_168h
            FROM (
                SELECT e.sensor_id, date_trunc('hour', e.occurred_at) AS hour
                FROM alarm_event e
                WHERE {_scope(level)} AND e.alarm_type = ANY(:state)
                  AND e.occurred_at >= :p - interval '168 hours' AND e.occurred_at < :p
                GROUP BY 1, 2
                HAVING count(*) > :blink
            ) hours
            """
            ),
            {
                "facility_id": facility_id,
                "p": point_of(at),
                "state": list(STATE_TYPES),
                "blink": BLINK_CHANGES_PER_HOUR,
            },
        )
        .mappings()
        .one()
    )
    return {key: float(value or 0) for key, value in row.items()}


@_cached
def _on_minutes(conn: Connection, facility_id: str, at: datetime, level: str) -> dict[str, float]:
    """Минуты во включённом состоянии по окнам.

    Насос включён от `PUMP_ON` до следующей смены того же канала, но не дольше
    суток и не дальше точки расчёта. Следующая смена ищется только среди
    записей до точки: событие после неё признак видеть не имеет права.
    """
    parts = ", ".join(
        f"""coalesce(sum(greatest(extract(epoch FROM (
                least(b, :p) - greatest(a, :p - interval '{w} hours'))), 0)) / 60.0, 0)
            AS on_min_{w}h"""
        for w in ON_WINDOWS
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
                  AND e.occurred_at >= :p - interval '{max(ON_WINDOWS) + MAX_ON_HOURS} hours'
                  AND e.occurred_at < :p
            ),
            iv AS (
                SELECT a, least(coalesce(nxt, a + interval '{MAX_ON_HOURS} hours'),
                                a + interval '{MAX_ON_HOURS} hours') AS b
                FROM s WHERE alarm_type = :on
            )
            SELECT {parts} FROM iv
            """
            ),
            {
                "facility_id": facility_id,
                "p": point_of(at),
                "state": list(STATE_TYPES),
                "on": PUMP_ON,
            },
        )
        .mappings()
        .one()
    )
    return {key: float(value or 0) for key, value in row.items()}


@_cached
def _unit_sensors(conn: Connection, facility_id: str, at: datetime) -> dict[str, float]:
    """Насосы и датчики затопления единицы.

    Первый источник — таблица `sensor`. Когда в ней нет строк единицы, число
    выводится из журнала: каналы, которые хоть раз подали сигнал до точки.
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
                  AND occurred_at < :p
                """
            ),
            {
                "facility_id": facility_id,
                "sensor_open": FLOOD_SENSOR_OPEN,
                "types": list(ALL_TYPES),
                "p": point_of(at),
            },
        ).one()
        pumps, sensors = int(fallback[0]), int(fallback[1])
    return {"pumps": float(pumps), "flood_sensors": float(sensors)}


@_cached
def _first_seen(conn: Connection, facility_id: str, at: datetime) -> datetime:
    """Начало часа первой записи единицы до точки. Нет записей значит сама точка."""
    p = point_of(at)
    row = conn.execute(
        text(
            "SELECT min(occurred_at) FROM alarm_event "
            "WHERE facility_id = :facility_id AND occurred_at < :p"
        ),
        {"facility_id": facility_id, "p": p},
    ).fetchone()
    first = row[0] if row and row[0] is not None else p
    return first.replace(minute=0, second=0, microsecond=0)


def _hours_since_last(conn: Connection, facility_id: str, at: datetime) -> float:
    """Часов от последнего часа с водой до точки расчёта.

    Воды не было значит часов от первой записи единицы плюс один, как в
    обучении: там отсчёт шёл от начала жизни единицы.
    """
    p = point_of(at)
    row = conn.execute(
        text(
            "SELECT max(e.occurred_at) FROM alarm_event e "
            "WHERE e.facility_id = :facility_id AND e.alarm_type = ANY(:label) "
            f"AND {WATER} AND e.occurred_at < :p"
        ),
        {"facility_id": facility_id, "label": list(LABEL_TYPES), "p": p},
    ).fetchone()
    if row and row[0] is not None:
        return (p - row[0].replace(minute=0, second=0, microsecond=0)).total_seconds() // 3600
    return (p - _first_seen(conn, facility_id, at)).total_seconds() // 3600 + 1


def _unit_age_days(conn: Connection, facility_id: str, at: datetime) -> float:
    """Смен московских суток от первой записи единицы до точки, как `date_diff('day')`."""
    return float((local_day(point_of(at)) - local_day(_first_seen(conn, facility_id, at))).days)


def _pump_share(conn: Connection, facility_id: str, at: datetime, hours: int) -> float:
    minutes = _on_minutes(conn, facility_id, at, "unit")[f"on_min_{hours}h"]
    pumps = max(_unit_sensors(conn, facility_id, at)["pumps"], 1.0)
    return minutes / (60.0 * hours * pumps)


def _pump_growth(conn: Connection, facility_id: str, at: datetime) -> float:
    on = _on_minutes(conn, facility_id, at, "unit")
    return (on["on_min_24h"] / 24.0) / max(on["on_min_720h"] / 720.0, 0.1)


@_cached
def _weather(conn: Connection, at: datetime) -> dict[str, float]:
    """Погода Москвы до точки расчёта, окна прошлых часов, как в `04_panel.py`."""
    row = (
        conn.execute(
            text(
                """
            SELECT
                sum(precip_mm) FILTER (WHERE observed_at >= :p - interval '24 hours')
                    AS precip_24h,
                sum(precip_mm) FILTER (WHERE observed_at >= :p - interval '72 hours')
                    AS precip_72h,
                sum(precip_mm) AS precip_168h,
                sum(rain_mm) FILTER (WHERE observed_at >= :p - interval '72 hours') AS rain_72h,
                sum(snowfall_cm) FILTER (WHERE observed_at >= :p - interval '72 hours')
                    AS snowfall_72h,
                avg(temperature_c) FILTER (WHERE observed_at >= :p - interval '24 hours')
                    AS temp_mean_24h,
                max(temperature_c) FILTER (WHERE observed_at >= :p - interval '72 hours')
                    AS temp_max_72h,
                max(snow_depth_m) FILTER (WHERE observed_at = :p - interval '1 hour')
                    AS snow_now,
                max(snow_depth_m) FILTER (WHERE observed_at = :p - interval '72 hours')
                    AS snow_72h_ago
            FROM weather_hourly
            WHERE district = :area
              AND observed_at >= :p - interval '168 hours' AND observed_at < :p
            """
            ),
            {"p": point_of(at), "area": WEATHER_AREA},
        )
        .mappings()
        .one()
    )
    snow_now = float(row["snow_now"] or 0.0)
    snow_before = float(row["snow_72h_ago"] or 0.0)
    return {
        **{
            key: float(row[key] or 0.0)
            for key in ("precip_24h", "precip_72h", "precip_168h", "rain_72h", "snowfall_72h")
        },
        "temp_mean_24h": _or_nan(row["temp_mean_24h"]),
        "temp_max_72h": _or_nan(row["temp_max_72h"]),
        "snow_depth_cm": snow_now * 100,
        "melt_72h_cm": max(0.0, snow_before - snow_now) * 100,
    }


def _or_nan(value: Any) -> float:
    return math.nan if value is None else float(value)


def _neighbour(source: Any, key: str, outer: str, inner: str) -> registry.Builder:
    """Признак соседей: сумма по внешнему уровню минус сумма по внутреннему."""

    def build(conn: Connection, facility_id: str, at: datetime) -> float:
        return source(conn, facility_id, at, outer)[key] - source(conn, facility_id, at, inner)[key]

    return build


def _cal(at: datetime) -> dict[str, float]:
    from app.features.calendar_ru import day_off_chain, is_day_off, is_holiday

    day = local_day(point_of(at))
    return {
        "hour": 0.0,
        "season": float((day.month % 12) // 3),
        "month": float(day.month),
        "day_of_year": float(day.timetuple().tm_yday),
        # Понедельник это ноль, как в `ml/common/calendar_ru.py::build_calendar`.
        "day_of_week": float(day.weekday()),
        "is_day_off": float(is_day_off(day)),
        "is_holiday": float(is_holiday(day)),
        "day_off_chain": float(day_off_chain(day)),
    }


def _unit(key: str) -> registry.Builder:
    return lambda c, f, a: _counts(c, f, a, "unit")[key]


def _wx(key: str) -> registry.Builder:
    return lambda c, _f, a: _weather(c, a)[key]


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
    # 2. история воды на единице
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
    "unit_age_days": _unit_age_days,
    # 7. свежесть внутри суток: вечер накануне весит больше утра
    "recent_evt_hours_6h": _unit("evt_hours_6h"),
    "recent_evt_hours_12h": _unit("evt_hours_12h"),
    "recent_check_hours_24h": _unit("check_hours_24h"),
    "recent_pump_on_share_6h": lambda c, f, a: _pump_share(c, f, a, 6),
    # 5. плановые проверки на пикете
    "check_hours_168h": _unit("check_hours_168h"),
    "check_hours_720h": _unit("check_hours_720h"),
    # 6. погода Москвы до точки расчёта
    "weather_precip_24h": _wx("precip_24h"),
    "weather_precip_72h": _wx("precip_72h"),
    "weather_precip_168h": _wx("precip_168h"),
    "weather_rain_72h": _wx("rain_72h"),
    "weather_snowfall_72h": _wx("snowfall_72h"),
    "weather_temp_mean_24h": _wx("temp_mean_24h"),
    "weather_temp_max_72h": _wx("temp_max_72h"),
    "weather_snow_depth_cm": _wx("snow_depth_cm"),
    "weather_melt_72h_cm": _wx("melt_72h_cm"),
    # 4. календарь. Час, день недели и нерабочий день модель ADR 0012 не
    # читает: она ищет воду, а не график работ. Построители остались для
    # моделей, которые их просят.
    "cal_hour": lambda _c, _f, a: _cal(a)["hour"],
    "cal_season": lambda _c, _f, a: _cal(a)["season"],
    "cal_month": lambda _c, _f, a: _cal(a)["month"],
    "cal_day_of_year": lambda _c, _f, a: _cal(a)["day_of_year"],
    "cal_day_of_week": lambda _c, _f, a: _cal(a)["day_of_week"],
    "cal_is_day_off": lambda _c, _f, a: _cal(a)["is_day_off"],
    "cal_is_holiday": lambda _c, _f, a: _cal(a)["is_holiday"],
    "cal_day_off_chain": lambda _c, _f, a: _cal(a)["day_off_chain"],
}
