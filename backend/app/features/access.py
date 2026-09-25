"""Словарь событий направления «несанкционированный доступ» и правило события.

Правило повторяет `ml/access/data.py`. Признаки поверх него считает
`app/features/access_daily.py`.

## Единица

Единица прогноза это участок хода вокруг узла входа: аварийного выхода, люка,
вентшахты или входной двери. Участков 400 (ADR 0001, правка от 25 сентября).
В схеме участок это строка `facility`, а объект, на котором он лежит, это
`facility.collector`. Признаки вида `obj_*` считаются по всем участкам того же
объекта.

## Словарь `alarm_type`

- `DOOR_OPEN` — контакт входа или датчик стекла: «Не замкнут» на каналах
  `КД АВ`, `КД Дверь`, `КД Люк`, `9-секционный люк`, `Стекло`.
- `MOTION` — «Обнаружено движение» на канале `Датчик движения`.
- `SECURITY_ARMED` и `SECURITY_DISARMED` — смена режима охраны. Режим это
  свойство объекта, поэтому приёмник пишет смену режима на каждый участок
  объекта.

Тревога доступа это `DOOR_OPEN` или `MOTION` (`ACCESS_ALARM_TYPES`).
`VOLUMETRIC` в доступ больше не входит. Это рычаг переговорного устройства
УИР-Р, то есть разговор с диспетчером, а не проход (ADR 0001).

Датчик участка лежит в таблице `sensor`. Контактный датчик несёт
`sensor_type = 'CONTACT'`. Правило события читает только их число.

## Момент, инцидент, событие

1. Момент это пара «датчик и отметка времени». Пачка строк в одну секунду весит
   как один момент.
2. Инцидент это моменты участка вне окон «Снято с охраны», между которыми
   прошло не больше `INCIDENT_GAP_MINUTES` минут.
3. Инцидент является событием, если выполнено одно из условий: в нём есть
   контакт, сработали два и больше датчиков движения, или участок держат
   меньше `TRUSTED_CONTACTS` контактных датчиков.

Условие `data.py` «датчик движения не дальше 6 пикетов от входа» здесь не
проверяется: схема не держит пикет датчика. Правило поэтому строже обучения:
одиночное движение у входа на участке с двумя контактами событием не станет.

Окно «Снято с охраны» без закрытия длится до конца отрезка расчёта.

Приёмник не пишет тревоги из тех отрезков, которые `data.py` вычёркивает до
правила: объект 5343 за 2020 и 2021 годы, первые 270 суток объекта и первые 30
суток канала. Правило события их поэтому не проверяет.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

CONTACT = "DOOR_OPEN"
MOTION = "MOTION"
ACCESS_ALARM_TYPES = (CONTACT, MOTION)
SECURITY_ARMED = "SECURITY_ARMED"
SECURITY_DISARMED = "SECURITY_DISARMED"
CONTACT_SENSOR_TYPE = "CONTACT"

INCIDENT_GAP_MINUTES = 30
TRUSTED_CONTACTS = 2


def event_ctes(scope: str) -> str:
    """Отдаёт CTE `access_moment` и `event_moment` для запроса `WITH`.

    `scope` это условие на строку `alarm_event e`: один участок, объект или
    вся сеть. Отрезок задают параметры `:ev_start` и `:ev_end`, их даёт
    `event_params`. Смена режима читается без левой границы: окно снятия могло
    открыться раньше отрезка.
    """
    return f"""
    guard AS (
        SELECT e.facility_id, e.occurred_at AS ts, e.alarm_type,
               lag(e.alarm_type) OVER (
                   PARTITION BY e.facility_id ORDER BY e.occurred_at, e.alarm_type
               ) AS prev
        FROM alarm_event e
        WHERE {scope} AND e.alarm_type IN (:armed, :disarmed) AND e.occurred_at < :ev_end
    ),
    guard_step AS (
        SELECT facility_id, ts, alarm_type,
               lead(ts) OVER (PARTITION BY facility_id ORDER BY ts) AS next_ts
        FROM guard WHERE prev IS NULL OR prev <> alarm_type
    ),
    disarm_window AS (
        SELECT facility_id, ts AS t_from, coalesce(next_ts, 'infinity') AS t_to
        FROM guard_step WHERE alarm_type = :disarmed
    ),
    access_moment AS (
        SELECT DISTINCT e.facility_id, e.sensor_id, e.occurred_at AS ts, e.alarm_type AS kind
        FROM alarm_event e
        WHERE {scope} AND e.alarm_type IN (:contact, :motion)
          AND e.occurred_at >= :ev_start AND e.occurred_at < :ev_end
    ),
    armed_moment AS (
        SELECT m.*,
               CASE WHEN m.ts - lag(m.ts) OVER w <= make_interval(mins => :gap)
                    THEN 0 ELSE 1 END AS is_new
        FROM access_moment m
        WHERE NOT EXISTS (
            SELECT 1 FROM disarm_window d
            WHERE d.facility_id = m.facility_id AND m.ts >= d.t_from AND m.ts < d.t_to
        )
        WINDOW w AS (PARTITION BY m.facility_id ORDER BY m.ts, m.sensor_id)
    ),
    incident AS (
        SELECT *, sum(is_new) OVER (
            PARTITION BY facility_id ORDER BY ts, sensor_id ROWS UNBOUNDED PRECEDING
        ) AS incident_no
        FROM armed_moment
    ),
    contacts AS (
        SELECT facility_id, count(*) AS n_contact FROM sensor
        WHERE sensor_type = :contact_sensor AND is_active GROUP BY facility_id
    ),
    valid_incident AS (
        SELECT i.facility_id, i.incident_no
        FROM incident i LEFT JOIN contacts c USING (facility_id)
        GROUP BY i.facility_id, i.incident_no
        HAVING bool_or(i.kind = :contact)
            OR count(DISTINCT i.sensor_id) FILTER (WHERE i.kind = :motion) >= 2
            OR coalesce(max(c.n_contact), 0) < :trusted
    ),
    event_moment AS (
        SELECT i.facility_id, i.sensor_id, i.ts
        FROM incident i
        JOIN valid_incident v USING (facility_id, incident_no)
    )
    """


def event_params(start: datetime, end: datetime) -> dict[str, Any]:
    """Параметры к `event_ctes`: отрезок `[start, end)` и словарь правила."""
    return {
        "ev_start": start,
        "ev_end": end,
        "armed": SECURITY_ARMED,
        "disarmed": SECURITY_DISARMED,
        "contact": CONTACT,
        "motion": MOTION,
        "contact_sensor": CONTACT_SENSOR_TYPE,
        "gap": INCIDENT_GAP_MINUTES,
        "trusted": TRUSTED_CONTACTS,
    }
