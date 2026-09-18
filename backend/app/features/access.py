"""Признаки направления «несанкционированный доступ» поверх `alarm_event`.

Имена и порядок признаков берутся из `ml/access/features.py::FEATURE_COLUMNS`
буква в букву: обучение и инференс обязаны видеть один и тот же вход.

## Словарь событий

`ml/access` считает признаки по сырой выгрузке СМВУ, где тип датчика и его
значение приходят отдельными полями (`stype`, `value`). Таблица `alarm_event`
такого разделения не держит: у неё одно поле `alarm_type` на строку. Приёмник
выгрузки (`app/pipeline`, задача T17/T18) ещё не написан, значит словарь
`alarm_type` для направления доступа никто не закрепил. Эта задача закрепляет
его сама, а не ждёт отдельного решения: закрепить его отдельно негде, кроме
как в точке, которая первой читает поле.

`alarm_type` для доступа принимает пять значений:

- `DOOR_OPEN` — дверной или люковый контакт разомкнут. Соответствует каналам
  `КД Дверь`, `КД Люк`, `КД АВ` со значением «Не замкнут» в сырой выгрузке.
- `VOLUMETRIC` — сработал объёмный датчик. Соответствует каналу
  `Состояние УИР-Р`.
- `MOTION` — сработал датчик движения. Соответствует каналу `Датчик движения`
  со значением «Обнаружено движение».
- `SECURITY_ARMED` — участок поставлен на охрану.
- `SECURITY_DISARMED` — участок снят с охраны.

`DOOR_OPEN`, `VOLUMETRIC`, `MOTION` считаются тревогой доступа
(`ACCESS_ALARM_TYPES`). `SECURITY_ARMED` и `SECURITY_DISARMED` тревогой не
являются: это переключение режима, а не событие на датчике, и в `n_alarms_*`
не входит. Приёмник выгрузки и генератор синтетики (`app/synth`) обязаны
писать `alarm_event.alarm_type` этими же пятью значениями, иначе признаки
посчитаются нулями молча.

## Единица признака и граница задачи

Единица обучения в `ml/access` — тройка «объект, галерея, пикет», 3 947 штук
(`hackathon-gap-analysis.md` §A2). Схема `backend` не хранит галерею и пикет
отдельными полями: `facility.id` уже и есть эта единица, а соседних пикетов
через `FeatureContext` не достать — таблицы соседства нет.

Отсюда два признака ml-версии сужены до границ одной `facility_id`, а не
окна в `NEAR_PICKETS` пикетов вдоль трассы:

- `neighbor_channels_1h` в `ml/access` уже считает каналы внутри одного пикета
  (см. docstring `ml/access/features.py`), поэтому граница не меняет смысл.
- `has_access_sequence` в `ml/access` ищет цепочку «дверь → объёмный датчик →
  движение» в радиусе `NEAR_PICKETS` пикетов. Здесь цепочка ищется только на
  той же `facility_id`. Это сужение, а не то же самое число: соседний пикет
  сюда не попадёт. Записано как документированное решение, не как измерение.

Каждый запрос читает `occurred_at < at`, никогда `<= at`: признак не имеет
права видеть момент расчёта и позже.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.ml.protocol import FeatureContext, FeatureVector

ACCESS_ALARM_TYPES = ("DOOR_OPEN", "VOLUMETRIC", "MOTION")
SECURITY_ARMED = "SECURITY_ARMED"
SECURITY_DISARMED = "SECURITY_DISARMED"
SEQUENCE_WINDOW_MINUTES = 15
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6


def _facility_first_seen(conn: Connection, facility_id: str, at: datetime) -> datetime:
    """Момент первой известной записи по объекту до `at`.

    Заменяет `hour_from` из `ml/access/out/access_units.parquet`: отдельной
    таблицы границ жизни единицы в схеме `backend` нет, а `hours_since_*`
    признакам нужно какое-то конечное число для единицы без истории тревог.
    Объект без единой записи в `alarm_event` получает в ответ `at`, то есть
    `hours_since_*` для него равен 0 — это отличается от `ml/access`, где
    единица без пикета в выборку вообще не попадает.
    """
    row = conn.execute(
        text(
            "SELECT min(occurred_at) FROM alarm_event "
            "WHERE facility_id = :facility_id AND occurred_at < :at"
        ),
        {"facility_id": facility_id, "at": at},
    ).fetchone()
    return row[0] if row and row[0] is not None else at


def _is_disarmed(conn: Connection, facility_id: str, at: datetime) -> bool:
    row = conn.execute(
        text(
            "SELECT alarm_type FROM alarm_event "
            "WHERE facility_id = :facility_id AND occurred_at < :at "
            "AND alarm_type IN (:armed, :disarmed) "
            "ORDER BY occurred_at DESC LIMIT 1"
        ),
        {
            "facility_id": facility_id,
            "at": at,
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
        },
    ).fetchone()
    return row is not None and row[0] == SECURITY_DISARMED


def _n_access_alarms(conn: Connection, facility_id: str, at: datetime, hours: int) -> int:
    row = conn.execute(
        text(
            "SELECT count(*) FROM alarm_event "
            "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) "
            "AND occurred_at >= :start AND occurred_at < :at"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=hours),
            "at": at,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _hours_since_last_access_alarm(conn: Connection, facility_id: str, at: datetime) -> int:
    row = conn.execute(
        text(
            "SELECT max(occurred_at) FROM alarm_event "
            "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) "
            "AND occurred_at < :at"
        ),
        {"facility_id": facility_id, "types": list(ACCESS_ALARM_TYPES), "at": at},
    ).fetchone()
    last = row[0] if row and row[0] is not None else _facility_first_seen(conn, facility_id, at)
    return int((at - last).total_seconds() // 3600)


def _alarm_hour_share_720h(conn: Connection, facility_id: str, at: datetime) -> float:
    row = conn.execute(
        text(
            "SELECT count(DISTINCT date_trunc('hour', occurred_at)) FROM alarm_event "
            "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) "
            "AND occurred_at >= :start AND occurred_at < :at"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=720),
            "at": at,
        },
    ).fetchone()
    hours = int(row[0]) if row else 0
    return hours / 720.0


def _night_share(conn: Connection, facility_id: str, at: datetime) -> float:
    row = conn.execute(
        text(
            "SELECT "
            "count(*) FILTER ("
            "WHERE extract(hour FROM occurred_at) >= :night_start "
            "OR extract(hour FROM occurred_at) < :night_end"
            "), "
            "count(*) "
            "FROM alarm_event "
            "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) "
            "AND occurred_at < :at"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "at": at,
            "night_start": NIGHT_START_HOUR,
            "night_end": NIGHT_END_HOUR,
        },
    ).fetchone()
    night = int(row[0]) if row and row[0] is not None else 0
    total = int(row[1]) if row and row[1] is not None else 0
    return night / total if total else 0.0


def _neighbor_channels_1h(conn: Connection, facility_id: str, at: datetime) -> int:
    row = conn.execute(
        text(
            "SELECT count(DISTINCT sensor_id) FROM alarm_event "
            "WHERE facility_id = :facility_id AND alarm_type = ANY(:types) "
            "AND occurred_at >= :start AND occurred_at < :at"
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "start": at - timedelta(hours=1),
            "at": at,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _has_access_sequence(conn: Connection, facility_id: str, at: datetime) -> bool:
    row = conn.execute(
        text(
            """
            WITH door AS (
                SELECT occurred_at AS ts FROM alarm_event
                WHERE facility_id = :facility_id AND alarm_type = 'DOOR_OPEN'
                  AND occurred_at < :at
            ),
            vol AS (
                SELECT d.ts AS door_ts, min(v.occurred_at) AS vol_ts
                FROM door d
                JOIN alarm_event v
                  ON v.facility_id = :facility_id AND v.alarm_type = 'VOLUMETRIC'
                  AND v.occurred_at > d.ts
                  AND v.occurred_at <= d.ts + make_interval(mins => :window_minutes)
                GROUP BY d.ts
            ),
            chain AS (
                SELECT v.door_ts, min(m.occurred_at) AS completed_ts
                FROM vol v
                JOIN alarm_event m
                  ON m.facility_id = :facility_id AND m.alarm_type = 'MOTION'
                  AND m.occurred_at > v.vol_ts
                  AND m.occurred_at <= v.door_ts + make_interval(mins => :window_minutes)
                GROUP BY v.door_ts
            )
            SELECT 1 FROM chain
            WHERE completed_ts < :at
              AND completed_ts >= :at - make_interval(mins => :window_minutes)
            LIMIT 1
            """
        ),
        {
            "facility_id": facility_id,
            "at": at,
            "window_minutes": SEQUENCE_WINDOW_MINUTES,
        },
    ).fetchone()
    return row is not None


def _n_armed_alarms(conn: Connection, facility_id: str, at: datetime, hours: int) -> int:
    row = conn.execute(
        text(
            """
            WITH toggle AS (
                SELECT occurred_at AS ts, alarm_type,
                       lead(occurred_at) OVER (ORDER BY occurred_at) AS next_ts
                FROM alarm_event
                WHERE facility_id = :facility_id
                  AND alarm_type IN (:armed, :disarmed)
                  AND occurred_at < :at
            ),
            window_ AS (
                SELECT ts AS win_start, coalesce(next_ts, :at) AS win_end
                FROM toggle
                WHERE alarm_type = :disarmed
            )
            SELECT count(*) FROM alarm_event a
            WHERE a.facility_id = :facility_id AND a.alarm_type = ANY(:types)
              AND a.occurred_at >= :start AND a.occurred_at < :at
              AND NOT EXISTS (
                  SELECT 1 FROM window_ w
                  WHERE a.occurred_at >= w.win_start AND a.occurred_at < w.win_end
              )
            """
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
            "start": at - timedelta(hours=hours),
            "at": at,
        },
    ).fetchone()
    return int(row[0]) if row else 0


def _hours_since_last_armed_alarm(conn: Connection, facility_id: str, at: datetime) -> int:
    row = conn.execute(
        text(
            """
            WITH toggle AS (
                SELECT occurred_at AS ts, alarm_type,
                       lead(occurred_at) OVER (ORDER BY occurred_at) AS next_ts
                FROM alarm_event
                WHERE facility_id = :facility_id
                  AND alarm_type IN (:armed, :disarmed)
                  AND occurred_at < :at
            ),
            window_ AS (
                SELECT ts AS win_start, coalesce(next_ts, :at) AS win_end
                FROM toggle
                WHERE alarm_type = :disarmed
            )
            SELECT max(a.occurred_at) FROM alarm_event a
            WHERE a.facility_id = :facility_id AND a.alarm_type = ANY(:types)
              AND a.occurred_at < :at
              AND NOT EXISTS (
                  SELECT 1 FROM window_ w
                  WHERE a.occurred_at >= w.win_start AND a.occurred_at < w.win_end
              )
            """
        ),
        {
            "facility_id": facility_id,
            "types": list(ACCESS_ALARM_TYPES),
            "armed": SECURITY_ARMED,
            "disarmed": SECURITY_DISARMED,
            "at": at,
        },
    ).fetchone()
    last = row[0] if row and row[0] is not None else None
    if last is None:
        last = _facility_first_seen(conn, facility_id, at)
    return int((at - last).total_seconds() // 3600)


def build_features(ctx: FeatureContext) -> FeatureVector:
    """Строит признаки направления доступа на момент `ctx.at`.

    Порядок и имена ключей совпадают с `ml/access/features.py::FEATURE_COLUMNS`.
    """
    conn = ctx.conn
    facility_id = ctx.facility_id
    at = ctx.at

    return {
        "n_alarms_1h": float(_n_access_alarms(conn, facility_id, at, 1)),
        "n_alarms_24h": float(_n_access_alarms(conn, facility_id, at, 24)),
        "n_alarms_168h": float(_n_access_alarms(conn, facility_id, at, 168)),
        "n_alarms_720h": float(_n_access_alarms(conn, facility_id, at, 720)),
        "alarm_hour_share_720h": _alarm_hour_share_720h(conn, facility_id, at),
        "hours_since_last_alarm": float(
            _hours_since_last_access_alarm(conn, facility_id, at)
        ),
        "night_share": _night_share(conn, facility_id, at),
        "hour_of_day": float(at.hour),
        "day_of_week": float((at.weekday() + 1) % 7),
        "month": float(at.month),
        "is_weekend": float(1 if (at.weekday() + 1) % 7 in (0, 6) else 0),
        "neighbor_channels_1h": float(_neighbor_channels_1h(conn, facility_id, at)),
        "is_disarmed": float(_is_disarmed(conn, facility_id, at)),
        "has_access_sequence": float(_has_access_sequence(conn, facility_id, at)),
        "n_armed_alarms_24h": float(_n_armed_alarms(conn, facility_id, at, 24)),
        "n_armed_alarms_168h": float(_n_armed_alarms(conn, facility_id, at, 168)),
        "hours_since_last_armed_alarm": float(
            _hours_since_last_armed_alarm(conn, facility_id, at)
        ),
    }
