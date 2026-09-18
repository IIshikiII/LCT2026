"""Синтетика для направления «несанкционированный доступ».

Настоящей выгрузки СМВУ в схеме `backend` нет: она приходит через будущий
приёмник (`app/ingest`, ещё не написан). До тех пор конвейер (`app/pipeline`)
и API нечем кормить на чистой базе после `docker compose up`. Этот модуль
наполняет три таблицы, которых достаточно для прогона: `collector`, `facility`
и `alarm_event` с событиями доступа (`app/features/access.py` держит словарь
типов). Полноту не выгрузки, а витрины: `sensor_reading`, `inspection`,
`weather_hourly` и прочие таблицы направлений здесь не наполняются, потому что
конвейер направления доступа их не читает, а расширять генератор ради данных,
которые никто не проверит, значит выбросить эту работу, когда придёт настоящая
выгрузка (см. `docs/05-gap-tasks.md`, задача 10).

## Детерминизм

Один `random.Random(seed)` на весь вызов `generate()`. Тот же `seed`, тот же
`facility_count` и тот же `now` дают побитово тот же набор строк: то же число
объектов, те же координаты, те же события доступа с теми же метками времени.
Без фиксации `now` вызов всё равно детерминирован в структуре (какие объекты
рискованные, какие типы тревог у них идут) — плавает только правая граница
истории, потому что генератор помещает события в окно `[now - HISTORY_DAYS,
now)`, чтобы `hours_since_last_alarm` не растил взгляд в прошлое с каждым
днём после посева.

## Повторный посев

`facility.id` и `collector.code` строятся из индекса, поэтому повтор с тем же
`seed` и `facility_count` бьёт в те же первичные ключи и уходит через
`ON CONFLICT DO NOTHING`: посев на уже наполненной базе не плодит дублей
объектов. `alarm_event.id` устроен так же — последовательный счётчик от
объекта и его тревог, — но `occurred_at` входит в тот же конфликт вместе с
`id`: набор строк не задваивается ровно потому, что оба вызова со старым
`now` строят один и тот же ряд отметок времени.

## Профиль риска

Часть объектов размечена «рискованными» (`RISKY_SHARE`): у них выше частота
тревог доступа и чаще встречается связка «дверь, объёмный датчик, движение»
(`smvu-insights.md` §2, признак `has_access_sequence` в
`app/features/access.py`). Без такого расслоения модель на синтетике не
увидела бы разницы между объектом и не о чем было бы объяснять через SHAP.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.features.access import ACCESS_ALARM_TYPES, SECURITY_ARMED, SECURITY_DISARMED
from app.meta.catalog import DISTRICTS
from app.tables import alarm_event, collector, facility

DEFAULT_SEED = 20260101
DEFAULT_FACILITY_COUNT = 60
FACILITIES_PER_COLLECTOR = 6
HISTORY_DAYS = 120
RISKY_SHARE = 0.2
MOSCOW_LAT = 55.751244
MOSCOW_LON = 37.618423
SPREAD_DEGREES = 0.35
FACILITY_TYPES = ("chamber", "manhole")
SEQUENCE_GAP_MINUTES = 5


@dataclass(frozen=True)
class SynthResult:
    """Итог одного вызова `generate()`. Для вывода в CLI."""

    collector_count: int
    facility_count: int
    alarm_event_count: int


@dataclass(frozen=True)
class _CollectorSeed:
    """Один коллектор до превращения в строку таблицы. Держит трассу типизированно,
    чтобы `_facility_rows` могла разложить точку без `type: ignore`."""

    code: str
    label: str
    district: str
    start: tuple[float, float]
    end: tuple[float, float]

    def as_row(self) -> dict[str, object]:
        return {
            "code": self.code,
            "label": self.label,
            "district": self.district,
            "line": [list(self.start), list(self.end)],
        }


def _collector_seeds(rng: random.Random, count: int) -> list[_CollectorSeed]:
    seeds = []
    for i in range(count):
        district = DISTRICTS[i % len(DISTRICTS)]
        start_lat = MOSCOW_LAT + rng.uniform(-SPREAD_DEGREES, SPREAD_DEGREES)
        start_lon = MOSCOW_LON + rng.uniform(-SPREAD_DEGREES, SPREAD_DEGREES)
        end_lat = start_lat + rng.uniform(-0.03, 0.03)
        end_lon = start_lon + rng.uniform(-0.03, 0.03)
        seeds.append(
            _CollectorSeed(
                code=f"K-{district.code}-{i + 1}",
                label=f"Коллектор {district.label.lower()} {i + 1}",
                district=district.code,
                start=(start_lon, start_lat),
                end=(end_lon, end_lat),
            )
        )
    return seeds


def _facility_rows(
    rng: random.Random, count: int, collectors: list[_CollectorSeed]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i in range(count):
        source = collectors[i % len(collectors)]
        lon_a, lat_a = source.start
        lon_b, lat_b = source.end
        share = rng.random()
        lon = lon_a + (lon_b - lon_a) * share
        lat = lat_a + (lat_b - lat_a) * share
        rows.append(
            {
                "id": f"F-{i + 1:04d}",
                "collector": source.code,
                "section": f"У-{i % 9 + 1}",
                "chamber": f"К-{i % 20 + 1}",
                "device": None,
                "district": source.district,
                "address": f"{source.label}, пикет {i + 1}",
                "lat": lat,
                "lon": lon,
                "facility_type": FACILITY_TYPES[i % len(FACILITY_TYPES)],
                "commissioned_at": None,
                "is_active": True,
            }
        )
    return rows


def _armed_windows(
    rng: random.Random, facility_id: str, sensor_id: str, start: datetime, end: datetime
) -> list[dict[str, object]]:
    """Чередование «на охране» и «снято с охраны» на весь период истории."""
    events: list[dict[str, object]] = []
    cursor = start
    armed = True
    while cursor < end:
        events.append(
            {
                "sensor_id": sensor_id,
                "facility_id": facility_id,
                "occurred_at": cursor,
                "alarm_type": SECURITY_DISARMED if armed else SECURITY_ARMED,
            }
        )
        armed = not armed
        cursor += timedelta(hours=rng.uniform(6, 30))
    return events


def _access_alarms(
    rng: random.Random,
    facility_id: str,
    sensor_ids: dict[str, str],
    start: datetime,
    end: datetime,
    is_risky: bool,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    rate_per_day = rng.uniform(1.5, 3.5) if is_risky else rng.uniform(0.05, 0.4)
    cursor = start
    while cursor < end:
        cursor += timedelta(hours=rng.expovariate(rate_per_day / 24))
        if cursor >= end:
            break
        if is_risky and rng.random() < 0.4:
            # Связка «дверь, объёмный датчик, движение» в коротком окне.
            events.append(
                {
                    "sensor_id": sensor_ids["DOOR_OPEN"],
                    "facility_id": facility_id,
                    "occurred_at": cursor,
                    "alarm_type": "DOOR_OPEN",
                }
            )
            step = timedelta(minutes=rng.uniform(1, SEQUENCE_GAP_MINUTES))
            cursor += step
            events.append(
                {
                    "sensor_id": sensor_ids["VOLUMETRIC"],
                    "facility_id": facility_id,
                    "occurred_at": cursor,
                    "alarm_type": "VOLUMETRIC",
                }
            )
            cursor += step
            events.append(
                {
                    "sensor_id": sensor_ids["MOTION"],
                    "facility_id": facility_id,
                    "occurred_at": cursor,
                    "alarm_type": "MOTION",
                }
            )
        else:
            alarm_type = rng.choice(ACCESS_ALARM_TYPES)
            events.append(
                {
                    "sensor_id": sensor_ids[alarm_type],
                    "facility_id": facility_id,
                    "occurred_at": cursor,
                    "alarm_type": alarm_type,
                }
            )
    return events


def _alarm_event_rows(
    rng: random.Random, facilities: list[dict[str, object]], now: datetime
) -> list[dict[str, object]]:
    start = now - timedelta(days=HISTORY_DAYS)
    rows: list[dict[str, object]] = []
    for row in facilities:
        facility_id = str(row["id"])
        is_risky = rng.random() < RISKY_SHARE
        sensor_ids = {
            "DOOR_OPEN": f"{facility_id}-DOOR",
            "VOLUMETRIC": f"{facility_id}-VOL",
            "MOTION": f"{facility_id}-MOTION",
        }
        security_sensor_id = f"{facility_id}-SECURITY"
        rows.extend(_armed_windows(rng, facility_id, security_sensor_id, start, now))
        rows.extend(_access_alarms(rng, facility_id, sensor_ids, start, now, is_risky))

    rows.sort(key=lambda item: (item["facility_id"], item["occurred_at"]))
    for next_id, row in enumerate(rows, start=1):
        row["id"] = next_id
    return rows


def generate(
    conn: Connection,
    *,
    seed: int = DEFAULT_SEED,
    facility_count: int = DEFAULT_FACILITY_COUNT,
    now: datetime | None = None,
) -> SynthResult:
    """Наполняет `collector`, `facility` и `alarm_event` синтетикой доступа."""
    rng = random.Random(seed)
    now = (now or datetime.now(UTC)).replace(microsecond=0)

    collector_count = ceil(facility_count / FACILITIES_PER_COLLECTOR)
    collector_seeds = _collector_seeds(rng, collector_count)
    facility_rows = _facility_rows(rng, facility_count, collector_seeds)
    event_rows = _alarm_event_rows(rng, facility_rows, now)
    collector_rows = [seed.as_row() for seed in collector_seeds]

    if collector_rows:
        conn.execute(
            pg_insert(collector).on_conflict_do_nothing(index_elements=["code"]),
            collector_rows,
        )
    if facility_rows:
        conn.execute(
            pg_insert(facility).on_conflict_do_nothing(index_elements=["id"]),
            facility_rows,
        )
    if event_rows:
        conn.execute(
            pg_insert(alarm_event).on_conflict_do_nothing(
                index_elements=["id", "occurred_at"]
            ),
            event_rows,
        )

    return SynthResult(
        collector_count=len(collector_rows),
        facility_count=len(facility_rows),
        alarm_event_count=len(event_rows),
    )
