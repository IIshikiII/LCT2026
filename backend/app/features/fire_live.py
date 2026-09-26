"""Живые блоки карточки пожара: датчики участка, графики, хроника. ADR 0018.

Правила ставят уровень участку, а бригада ищет место по датчику. Поэтому
карточка перечисляет все датчики пожарной сигнализации, температуры и метана
на пикетах участка, у каждого показывает сигналы за сутки и отмечает датчик,
к которому очаг ближе всего: первый сигнал вне пачки обхода.

Ряд графика без единого ненулевого значения в блок не идёт. Температура
участка берётся с его датчиков, без них с датчиков объекта.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features import fire
from app.ml.protocol import Block

MOSCOW = timedelta(hours=3)
BARS = "▁▂▃▄▅▆▇█"
KIND_LABEL = {
    fire.SMOKE_DETECTED: "Обнаружен дым",
    fire.HEAT_OPEN: "Сработал тепловой датчик",
    fire.TEMP_HIGH: "Температура выше 40 °C",
}
SENSOR_LABEL = {
    fire.SENSOR_SMOKE: "дым",
    fire.SENSOR_HEAT: "тепловой",
    fire.SENSOR_TEMPERATURE: "температура",
    fire.SENSOR_METHANE: "метан",
}


def _clock(at: datetime) -> str:
    return (at.astimezone(UTC) + MOSCOW).strftime("%d.%m %H:%M")


def spark(counts: list[int]) -> str:
    """Строка-график из счётчиков: пустое окно это точка, полное это столбик."""
    top = max(counts) if counts else 0
    if top == 0:
        return "·" * len(counts)
    return "".join(
        "·" if c == 0 else BARS[min(len(BARS) - 1, round((len(BARS) - 1) * c / top))]
        for c in counts
    )


def _sensors(conn: Connection, unit: list[str]) -> list[Any]:
    return list(
        conn.execute(
            text(
                """
            SELECT s.id, s.sensor_type, coalesce(s.channel_name, s.id) AS name,
                   f.picket, f.id AS facility_id
            FROM sensor s JOIN facility f ON f.id = s.facility_id
            WHERE s.facility_id = ANY(:unit) AND s.sensor_type = ANY(:types)
            ORDER BY f.chainage_m NULLS LAST, s.id
            """
            ),
            {"unit": unit, "types": [*fire.FIRE_SENSOR_TYPES, fire.SENSOR_METHANE]},
        ).all()
    )


def _focus(signals: list[fire.Signal]) -> str | None:
    """Датчик первого сигнала вне пачки. Без таких сигналов датчик с наибольшим
    числом сигналов."""
    single = [s for s in signals if not fire.is_burst(s, signals)]
    if single:
        return single[0].sensor_id
    if not signals:
        return None
    counts: dict[str, int] = defaultdict(int)
    for s in signals:
        counts[s.sensor_id] += 1
    return max(counts, key=lambda k: counts[k])


def sensors_block(conn: Connection, facility_id: str, at: datetime) -> Block | None:
    """Таблица датчиков участка с историей каждого за сутки."""
    unit = fire.members(conn, facility_id)
    sensors = _sensors(conn, unit)
    if not sensors:
        return None
    all_signals, _, _ = fire.signals_of(conn, facility_id, at)
    start = at - timedelta(hours=fire.WINDOW_HOURS)
    own = [s for s in all_signals if s.facility_id in set(unit) and s.at >= start]
    focus = _focus(own)
    by_sensor: dict[str, list[fire.Signal]] = defaultdict(list)
    for s in own:
        by_sensor[s.sensor_id].append(s)

    rows = []
    for sid, stype, name, picket, _fid in sensors:
        mine = by_sensor.get(sid, [])
        buckets = [0] * fire.BUCKETS
        for s in mine:
            back = int((at - s.at).total_seconds() // (fire.BUCKET_HOURS * 3600))
            if 0 <= back < fire.BUCKETS:
                buckets[fire.BUCKETS - 1 - back] += 1
        rows.append(
            {
                "focus": "★ очаг ближе" if sid == focus else "",
                "sensor": str(name),
                "type": SENSOR_LABEL.get(str(stype), str(stype)),
                "picket": f"ПК{int(picket)}" if picket is not None else "—",
                "signals": len(mine),
                "history": spark(buckets),
                "last": _clock(mine[-1].at) if mine else "—",
            }
        )
    rows.sort(key=lambda r: (r["focus"] == "", -int(r["signals"])))
    return Block(
        type="table",
        title="Датчики участка",
        data={
            "columns": [
                {"key": "focus", "header": ""},
                {"key": "sensor", "header": "Датчик"},
                {"key": "type", "header": "Тип"},
                {"key": "picket", "header": "Пикет"},
                {"key": "signals", "header": "Сигналов за сутки", "align": "right"},
                {"key": "history", "header": "По 4 ч"},
                {"key": "last", "header": "Последний"},
            ],
            "rows": rows,
        },
    )


LOOKBACK_DAYS = 7


def _hourly_temperature(conn: Connection, facility_id: str, at: datetime) -> list[Any]:
    """Температура участка по часам за сутки. Без датчиков участка объект.

    Датчик температуры пишет при изменении значения, а не каждый час. Час без
    записи держит последнее известное значение, как держит его сам датчик.
    Последнее значение ищется на неделю назад.
    """
    unit = fire.members(conn, facility_id)
    start = (at - timedelta(hours=fire.WINDOW_HOURS)).replace(minute=0, second=0, microsecond=0)
    params = {
        "unit": unit,
        "metric": fire.METRIC_TEMPERATURE,
        "since": start - timedelta(days=LOOKBACK_DAYS),
        "at": at,
        "id": facility_id,
    }
    scopes = (
        "r.facility_id = ANY(:unit)",
        "f.collector = (SELECT collector FROM facility WHERE id = :id)",
    )
    rows: list[Any] = []
    for scope in scopes:
        rows = list(
            conn.execute(
                text(
                    f"""
                    SELECT date_trunc('hour', r.observed_at) AS h, max(r.value)
                    FROM sensor_reading r JOIN facility f ON f.id = r.facility_id
                    WHERE {scope} AND r.metric = :metric
                      AND r.observed_at >= :since AND r.observed_at < :at
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                params,
            ).all()
        )
        if rows:
            break
    known = {h: float(v) for h, v in rows}
    last = next((v for h, v in reversed(rows) if h < start), None)
    grid = []
    for i in range(fire.WINDOW_HOURS + 1):
        hour = start + timedelta(hours=i)
        if hour >= at:
            break
        last = known.get(hour, last)
        if last is not None:
            grid.append((hour, float(last)))
    return grid


def series_block(conn: Connection, facility_id: str, at: datetime) -> Block | None:
    """Температура и сигналы участка по часам за сутки."""
    series: list[dict[str, Any]] = []
    temps = _hourly_temperature(conn, facility_id, at)
    if temps and any(float(v) != 0.0 for _, v in temps):
        series.append(
            {
                "name": "Температура участка",
                "unit": "°C",
                "points": [{"t": h.isoformat(), "v": round(float(v), 1)} for h, v in temps],
            }
        )
    unit = set(fire.members(conn, facility_id))
    all_signals, _, _ = fire.signals_of(conn, facility_id, at)
    start = at - timedelta(hours=fire.WINDOW_HOURS)
    counts = [0] * fire.WINDOW_HOURS
    for s in all_signals:
        if s.facility_id in unit and s.at >= start:
            counts[min(fire.WINDOW_HOURS - 1, int((s.at - start).total_seconds() // 3600))] += 1
    if any(counts):
        series.append(
            {
                "name": "Сигналы пожарной сигнализации за час",
                "unit": "шт.",
                "points": [
                    {"t": (start + timedelta(hours=i, minutes=30)).isoformat(), "v": float(c)}
                    for i, c in enumerate(counts)
                ],
            }
        )
    if not series:
        air = conn.execute(
            text(
                """
                SELECT observed_at, temperature_c FROM weather_hourly
                WHERE district = 'MOSCOW' AND temperature_c IS NOT NULL
                  AND observed_at >= :start AND observed_at < :at
                ORDER BY 1
                """
            ),
            {"start": start, "at": at},
        ).all()
        if not air:
            return None
        series.append(
            {
                "name": "Температура воздуха в Москве",
                "unit": "°C",
                "points": [{"t": h.isoformat(), "v": round(float(v), 1)} for h, v in air],
            }
        )
    return Block(
        type="timeseries",
        title="Участок за сутки",
        data={"series": series, "markerAt": at.isoformat()},
    )


def timeline_block(conn: Connection, facility_id: str, at: datetime) -> Block:
    """Хроника сигналов участка с именем датчика. Последние сверху."""
    unit = set(fire.members(conn, facility_id))
    names = {
        str(sid): (str(name), picket) for sid, _t, name, picket, _f in _sensors(conn, sorted(unit))
    }
    all_signals, _, _ = fire.signals_of(conn, facility_id, at)
    start = at - timedelta(hours=fire.WINDOW_HOURS)
    events: list[dict[str, Any]] = []
    for s in all_signals:
        if s.facility_id not in unit or s.at < start:
            continue
        name, picket = names.get(s.sensor_id, (s.sensor_id, None))
        where = f"{name}, ПК{int(picket)}" if picket is not None else name
        burst = fire.is_burst(s, all_signals)
        events.append(
            {
                "at": s.at.isoformat(),
                "title": f"{KIND_LABEL.get(s.kind, s.kind)} · {where}",
                "kind": "alarm",
                "note": "пачка по объекту: обход или сбой линии" if burst else "одиночный сигнал",
            }
        )
    events.append(
        {"at": at.isoformat(), "title": "Прогноз обновлён", "kind": "forecast", "note": ""}
    )
    events.sort(key=lambda e: str(e["at"]), reverse=True)
    return Block(type="timeline", title="Сигналы участка за сутки", data={"events": events[:40]})


def blocks(conn: Connection, facility_id: str, at: datetime) -> list[Block]:
    found = [
        sensors_block(conn, facility_id, at),
        series_block(conn, facility_id, at),
        timeline_block(conn, facility_id, at),
    ]
    return [b for b in found if b is not None]
