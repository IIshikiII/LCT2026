"""Демонстрационные сценарии пожара в потоке СМВУ. ADR 0018.

Настоящих пожаров в выгрузке нет (ADR 0014), и за неделю потока правила почти
всегда ставят низкий уровень. Чтобы диспетчер увидел работу правил на всех
уровнях, заглушка подмешивает в поток строки журнала, похожие на развитие
пожара. Уровень им ставят те же экспертные правила, что и настоящим данным:
сценарий задаёт сигналы, а не уровень.

- `CRITICAL`: дым, через 4 минуты тепловой датчик, если он есть на объекте,
  через 12 минут дым соседнего пикета, температура растёт на 26 °C за полтора
  часа. Правила видят дым, подтверждённый теплом, или переход сигнала на
  соседа при росте температуры. Без теплового датчика карточка сначала
  высокая и становится критической, когда температура поднимется на 10 °C.
- `HIGH`: дым, через 15 минут дым соседнего пикета не дальше 50 м. Правила
  видят переход сигнала на соседа.
- `MEDIUM`: одиночный дым ночью или в выходной. Правила видят ночь без обхода.

Частота выбрана так, чтобы за сутки критических было меньше высоких, а высоких
меньше средних. Участок не получает второй сценарий раньше чем через
`COOLDOWN`. Кандидатов готовит загрузчик: участки с двумя датчиками дыма рядом
по линии, тепловым и температурным датчиками на объекте и без охлаждения.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

SMOKE = "Обнаружен дым"
HEAT = "Не замкнут"
COOLDOWN = timedelta(hours=26)
# Сценариев в час. Средний возможен только ночью и в выходной.
RATES: dict[str, float] = {"CRITICAL": 0.1, "HIGH": 0.25, "MEDIUM": 1.5}
EVENT_ID_BASE = 9_000_000_000


@dataclass(frozen=True)
class Row:
    """Строка журнала сценария в местном времени Москвы."""

    at: datetime
    channel_id: int
    alarm: bool
    value: str
    kind: str

    def as_event(self, event_id: int) -> dict[str, Any]:
        return {
            "ид_события": event_id,
            "ид_канала_данных": self.channel_id,
            "дата": self.at.strftime("%Y-%m-%d"),
            "время": self.at.strftime("%H:%M:%S"),
            "тревожное": "true" if self.alarm else "false",
            "значение_датчика": self.value,
        }


def off_hours(moment: datetime, day_off: Any) -> bool:
    """Ночь с 22 до 6 или нерабочий день."""
    return moment.hour < 6 or moment.hour >= 22 or bool(day_off(moment.date()))


def _pair(candidate: dict[str, Any], rng: random.Random) -> tuple[int, int]:
    first, second = rng.choice(candidate["pairs"])
    return (first, second) if rng.random() < 0.5 else (second, first)


def fits(kind: str, candidate: dict[str, Any]) -> bool:
    """Годится ли участок для сценария."""
    if kind == "CRITICAL":
        return bool(candidate.get("pairs")) and bool(candidate.get("temp"))
    if kind == "HIGH":
        return bool(candidate.get("pairs"))
    return bool(candidate.get("smoke") or candidate.get("pairs"))


def rows_of(kind: str, candidate: dict[str, Any], at: datetime, rng: random.Random) -> list[Row]:
    """Строки одного сценария на участке `candidate` от момента `at`."""
    if kind == "MEDIUM":
        smoke = candidate.get("smoke") or [p[0] for p in candidate["pairs"]]
        return [Row(at, rng.choice(smoke), True, SMOKE, kind)]
    first, second = _pair(candidate, rng)
    rows = [Row(at, first, True, SMOKE, kind)]
    if kind == "CRITICAL":
        if candidate.get("heat"):
            heat = rng.choice(candidate["heat"])
            rows.append(Row(at + timedelta(minutes=4), heat, True, HEAT, kind))
        rows.append(Row(at + timedelta(minutes=12), second, True, SMOKE, kind))
        base = rng.uniform(19.0, 25.0)
        temp = rng.choice(candidate["temp"])
        for step in range(-6, 10):
            rise = 0.0 if step < 0 else min(26.0, 3.2 * step)
            value = base + rise + rng.uniform(-0.4, 0.4)
            rows.append(Row(at + timedelta(minutes=10 * step), temp, False, f"{value:.1f}", kind))
    elif kind == "HIGH":
        rows.append(Row(at + timedelta(minutes=15), second, True, SMOKE, kind))
    return sorted(rows, key=lambda r: r.at)


def _poisson(rng: random.Random, rate: float) -> int:
    """Число событий пуассоновского потока за час. Алгоритм Кнута."""
    limit, k, p = math.exp(-rate), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def plan(
    candidates: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    rng: random.Random,
    day_off: Any,
    busy: dict[str, datetime] | None = None,
) -> list[Row]:
    """Сценарии на отрезок `[start, end)` местного времени, час за часом.

    `busy` помнит, когда участок получил последний сценарий. Заглушка держит
    его между вызовами, чтобы участок не горел дважды подряд.
    """
    if not candidates:
        return []
    busy = busy if busy is not None else {}
    rows: list[Row] = []
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour < end:
        for kind, rate in RATES.items():
            for _ in range(_poisson(rng, rate)):
                moment = hour + timedelta(seconds=rng.uniform(0, 3600))
                if moment < start or moment >= end:
                    continue
                if kind == "MEDIUM" and not off_hours(moment, day_off):
                    continue
                free = [
                    c
                    for c in candidates
                    if fits(kind, c)
                    if moment - busy.get(c["section"], datetime.min) > COOLDOWN
                ]
                if not free:
                    continue
                candidate = rng.choice(free)
                busy[candidate["section"]] = moment
                rows.extend(rows_of(kind, candidate, moment, rng))
        hour += timedelta(hours=1)
    return sorted(rows, key=lambda r: r.at)


def never_day_off(_: date) -> bool:
    return False
