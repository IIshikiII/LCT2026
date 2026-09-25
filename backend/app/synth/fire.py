"""Синтетика направления «пожарный риск». ADR 0016.

Форма повторяет то, что показала выгрузка (ADR 0014, ADR 0015):

- обходы с проверкой: в будни днём техник проходит объект, и датчики дыма
  его пикетов тревожат пачкой с шагом в минуты;
- одиночные ложные сигналы: редкие, в любое время;
- обесточенная фаза в комплексе: частая и к пожару не относится;
- суточный ход температуры коллектора.

Для демонстрации каждого уровня кладутся свежие эпизоды за последние сутки:

- CRITICAL: дым, подтверждённый тепловым датчиком, и рост температуры;
- HIGH: сигнал перешёл на соседний пикет объекта;
- MEDIUM: одиночный сигнал ночью, обхода в объекте не было;
- новый датчик в периоде охлаждения: уровень понижен;
- метан внутри окна ППР: тревоги нет, это проверка.

Случайность своя, от `seed + 2`. Потоки доступа и подтопления от неё не
меняются.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from app.features.calendar_ru import is_day_off
from app.features.fire import (
    HEAT_OPEN,
    METRIC_METHANE,
    METRIC_TEMPERATURE,
    MOSCOW,
    PHASE_OFF,
    SENSOR_HEAT,
    SENSOR_METHANE,
    SENSOR_SMOKE,
    SENSOR_TEMPERATURE,
    SMOKE_DETECTED,
    TEMP_HIGH,
)

WALK_EVERY_DAYS = (10.0, 18.0)
FALSE_ALARMS_PER_YEAR = 0.6
PHASE_OFF_PER_DAY = 0.6
HEAT_SHARE = 0.35
INSTALLED_YEARS_AGO = 2


def _event(facility_id: str, sensor_id: str, kind: str, at: datetime) -> dict[str, Any]:
    return {
        "sensor_id": sensor_id,
        "facility_id": facility_id,
        "occurred_at": at,
        "alarm_type": kind,
    }


def _local_moment(day: date, hour: float) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=MOSCOW) + timedelta(hours=hour)


def fire_rows(
    seed: int, facilities: list[dict[str, Any]], now: datetime, history_days: int
) -> dict[str, list[dict[str, Any]]]:
    """Строки `sensor`, `alarm_event`, `sensor_reading`, `maintenance_window`
    и отметки пусконаладки объектов."""
    rng = random.Random(seed + 2)
    start = now - timedelta(days=history_days)
    # Обычные датчики стояли задолго до начала истории. Иначе каждый объект
    # оказался бы моложе периода охлаждения, и правила понизили бы все уровни.
    installed = (start - timedelta(days=INSTALLED_YEARS_AGO * 365)).date()
    sensors: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    readings: list[dict[str, Any]] = []
    by_collector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    smoke: dict[str, str] = {}
    heat: dict[str, str] = {}
    for row in facilities:
        by_collector[str(row["collector"])].append(row)
        facility_id = str(row["id"])
        smoke[facility_id] = f"{facility_id}-SMOKE"
        sensors.append(
            {
                "id": smoke[facility_id],
                "facility_id": facility_id,
                "sensor_type": SENSOR_SMOKE,
                "installed_at": installed,
            }
        )
        if rng.random() < HEAT_SHARE:
            heat[facility_id] = f"{facility_id}-HEAT"
            sensors.append(
                {
                    "id": heat[facility_id],
                    "facility_id": facility_id,
                    "sensor_type": SENSOR_HEAT,
                    "installed_at": installed,
                }
            )

    collectors = sorted(by_collector)
    # Температура: один датчик на объект, на первом пикете.
    for code in collectors:
        first = by_collector[code][0]
        facility_id = str(first["id"])
        sensor_id = f"{facility_id}-TEMP"
        sensors.append(
            {
                "id": sensor_id,
                "facility_id": facility_id,
                "sensor_type": SENSOR_TEMPERATURE,
                "installed_at": installed,
            }
        )
        base = rng.uniform(12.0, 18.0)
        moment = start.replace(minute=0, second=0)
        while moment < now:
            daily = 1.5 * math.sin(2 * math.pi * (moment.hour - 9) / 24)
            readings.append(
                {
                    "sensor_id": sensor_id,
                    "facility_id": facility_id,
                    "metric": METRIC_TEMPERATURE,
                    "observed_at": moment,
                    "value": round(base + daily + rng.gauss(0, 0.4), 2),
                    "unit": "°C",
                }
            )
            moment += timedelta(hours=1)

    # Обходы: будни, 10–12 часов по Москве, все пикеты объекта подряд.
    for code in collectors:
        day = start.astimezone(MOSCOW).date() + timedelta(days=rng.randint(0, 9))
        end_day = (now - timedelta(days=2)).astimezone(MOSCOW).date()
        while day < end_day:
            if not is_day_off(day):
                at = _local_moment(day, rng.uniform(10, 12))
                for row in by_collector[code]:
                    events.append(_event(str(row["id"]), smoke[str(row["id"])], SMOKE_DETECTED, at))
                    at += timedelta(minutes=rng.uniform(1.5, 4.0))
            day += timedelta(days=int(rng.uniform(*WALK_EVERY_DAYS)))

    # Одиночные ложные сигналы в прошлом.
    for row in facilities:
        facility_id = str(row["id"])
        cursor = start
        while True:
            cursor += timedelta(days=rng.expovariate(FALSE_ALARMS_PER_YEAR / 365))
            if cursor >= now - timedelta(days=2):
                break
            events.append(_event(facility_id, smoke[facility_id], SMOKE_DETECTED, cursor))

    # Обесточенная фаза в комплексе: часто и без связи с пожаром.
    districts: dict[str, str] = {}
    for row in facilities:
        districts.setdefault(str(row["district"]), str(row["id"]))
    for district, facility_id in sorted(districts.items()):
        sensor_id = f"{facility_id}-PHASE"
        sensors.append(
            {
                "id": sensor_id,
                "facility_id": facility_id,
                "sensor_type": "PHASE",
                "installed_at": installed,
            }
        )
        cursor = start
        while True:
            cursor += timedelta(days=rng.expovariate(PHASE_OFF_PER_DAY))
            if cursor >= now:
                break
            events.append(_event(facility_id, sensor_id, PHASE_OFF, cursor))
        del district

    demo = _demo(rng, by_collector, collectors, smoke, heat, sensors, readings, now)
    events.extend(demo["events"])
    return {
        "sensors": sensors,
        "events": [e for e in events if start <= e["occurred_at"] < now],
        "readings": [r for r in readings if r["observed_at"] < now],
        "windows": demo["windows"],
        "commissioning": demo["commissioning"],
    }


def _demo(
    rng: random.Random,
    by_collector: dict[str, list[dict[str, Any]]],
    collectors: list[str],
    smoke: dict[str, str],
    heat: dict[str, str],
    sensors: list[dict[str, Any]],
    readings: list[dict[str, Any]],
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Свежие эпизоды по одному на уровень. Разные объекты, чтобы не мешали."""
    events: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    commissioning: list[dict[str, Any]] = []
    picks = [c for c in collectors if len(by_collector[c]) >= 3]
    if len(picks) < 5:
        return {"events": events, "windows": windows, "commissioning": commissioning}

    # CRITICAL: дым и тепловой сигнал другого датчика, температура растёт.
    code = picks[0]
    target = by_collector[code][1]
    facility_id = str(target["id"])
    heat_id = heat.get(facility_id) or f"{facility_id}-HEAT"
    if facility_id not in heat:
        heat[facility_id] = heat_id
        sensors.append(
            {
                "id": heat_id,
                "facility_id": facility_id,
                "sensor_type": SENSOR_HEAT,
                "installed_at": (now - timedelta(days=90)).date(),
            }
        )
    at = now - timedelta(hours=2, minutes=10)
    events.append(_event(facility_id, smoke[facility_id], SMOKE_DETECTED, at))
    events.append(_event(facility_id, heat_id, HEAT_OPEN, at + timedelta(minutes=12)))
    temp_facility = str(by_collector[code][0]["id"])
    for reading in readings:
        if (
            reading["facility_id"] == temp_facility
            and reading["metric"] == METRIC_TEMPERATURE
            and at - timedelta(hours=1) <= reading["observed_at"] < now
        ):
            reading["value"] = round(reading["value"] + 14.0, 2)
    events.append(
        _event(temp_facility, f"{temp_facility}-TEMP", TEMP_HIGH, at + timedelta(minutes=25))
    )

    # HIGH: сигнал перешёл на соседний пикет.
    code = picks[1]
    first, second = by_collector[code][0], by_collector[code][1]
    at = now - timedelta(hours=5, minutes=30)
    events.append(_event(str(first["id"]), smoke[str(first["id"])], SMOKE_DETECTED, at))
    events.append(
        _event(
            str(second["id"]), smoke[str(second["id"])], SMOKE_DETECTED, at + timedelta(minutes=14)
        )
    )

    # MEDIUM: одиночный сигнал в последнюю ночь, 2:30 по Москве.
    code = picks[2]
    target = by_collector[code][2]
    local_now = now.astimezone(MOSCOW)
    night = local_now.replace(hour=2, minute=30, second=0, microsecond=0)
    if night >= local_now:
        night -= timedelta(days=1)
    events.append(_event(str(target["id"]), smoke[str(target["id"])], SMOKE_DETECTED, night))

    # Охлаждение: новый датчик поставлен пять суток назад и уже тревожил ночью.
    code = picks[3]
    target = by_collector[code][1]
    facility_id = str(target["id"])
    new_id = f"{facility_id}-SMOKE-NEW"
    sensors.append(
        {
            "id": new_id,
            "facility_id": facility_id,
            "sensor_type": SENSOR_SMOKE,
            "installed_at": (now - timedelta(days=5)).date(),
        }
    )
    events.append(_event(facility_id, new_id, SMOKE_DETECTED, night + timedelta(minutes=40)))

    # Газ внутри окна ППР: показание 1,6 % идёт проверкой, тревоги нет.
    code = picks[4]
    target = by_collector[code][0]
    facility_id = str(target["id"])
    gas_id = f"{facility_id}-CH4"
    sensors.append(
        {
            "id": gas_id,
            "facility_id": facility_id,
            "sensor_type": SENSOR_METHANE,
            "installed_at": (now - timedelta(days=100)).date(),
        }
    )
    today = local_now.date()
    windows.append(
        {
            "collector": code,
            "kind": "PPR_METHANE",
            "starts_on": today - timedelta(days=3),
            "ends_on": today + timedelta(days=5),
            "source": "синтетика: пример графика ППР",
        }
    )
    moment = now - timedelta(hours=20)
    while moment < now:
        value = 1.6 if abs((moment - (now - timedelta(hours=6))).total_seconds()) < 1800 else 0.02
        readings.append(
            {
                "sensor_id": gas_id,
                "facility_id": facility_id,
                "metric": METRIC_METHANE,
                "observed_at": moment,
                "value": value,
                "unit": "%",
            }
        )
        moment += timedelta(minutes=30)

    # Пусконаладка: объект с отметкой эксплуатации на две недели вперёд.
    commissioning.append({"code": collectors[-1], "until": today + timedelta(days=14)})
    del rng
    return {"events": events, "windows": windows, "commissioning": commissioning}
