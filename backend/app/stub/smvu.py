"""Заглушка потока СМВУ. ADR 0017.

Настоящий поток СМВУ заказчик не даст (QA-сессия). Заглушка проигрывает
неделю из выгрузки так, как её слал бы СМВУ: строка журнала уходит в API в
тот момент, когда наступает её время.

## Время

Загрузчик выгрузки сдвинул историю на целое число недель так, что она
кончается в момент загрузки (`app/ingest/dataset.py`). Заглушка берёт тот же
сдвиг из `slice.json` и продолжает историю с той же точки. Неделя кончилась
значит следующий круг: та же неделя, сдвинутая ещё на семь суток. День недели
и время суток при этом сохраняются.

Заглушка, запущенная позже, начинает с текущей точки недели. Пропущенное
время она не досылает: догонять неделю пачкой значило бы нагрузить сервер
разом, а в журнале остался бы провал. Провал закрывает повторный запуск
загрузчика.

## Нагрузка

Отрезок держит только каналы, которые знает база, и только строки событий и
чисел температуры и метана. Это около 140 тысяч строк в сутки, почти все
показания метана: полторы строки в секунду. Пачка уходит раз в
`BATCH_SECONDS`.

Поверх недели заглушка подмешивает сценарии пожара (`app/stub/scenarios.py`,
ADR 0018): без них правила пожара за неделю почти не выходят из низкого
уровня.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import random
import signal
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

from app.features.calendar_ru import is_day_off
from app.stub import scenarios

log = logging.getLogger(__name__)

MOSCOW = timedelta(hours=3)
BATCH_SECONDS = 2.0
BATCH_LIMIT = 5000
DEFAULT_API = "http://127.0.0.1:8000/api/v1/stream/events"


@dataclass(frozen=True)
class Row:
    """Строка отрезка: смещение от начала недели и поля журнала."""

    offset_s: float
    event_id: str
    channel_id: str
    alarm: str
    value: str


@dataclass(frozen=True)
class Clock:
    """Часы проигрывания. `origin` это начало недели первого круга в UTC."""

    origin: datetime
    length: timedelta

    @staticmethod
    def from_meta(meta: dict[str, Any]) -> Clock:
        start = datetime.fromisoformat(meta["slice_start"])
        shifted = start + timedelta(days=int(meta["shift_days"]))
        return Clock(
            origin=(shifted - MOSCOW).replace(tzinfo=UTC),
            length=timedelta(days=int(meta.get("slice_days", 7))),
        )

    def position(self, now: datetime) -> tuple[int, float]:
        """Номер круга и смещение от начала недели для момента `now`."""
        elapsed = (now - self.origin).total_seconds()
        span = self.length.total_seconds()
        loop = int(elapsed // span) if elapsed >= 0 else 0
        return loop, max(0.0, elapsed - loop * span)

    def moment(self, loop: int, offset_s: float) -> datetime:
        """Момент строки на круге `loop`, UTC."""
        return self.origin + self.length * loop + timedelta(seconds=offset_s)


def read_slice(stream_dir: Path) -> tuple[Clock, list[Row]]:
    """Читает отрезок и его описание из папки загрузчика."""
    meta = json.loads((stream_dir / "slice.json").read_text(encoding="utf-8"))
    clock = Clock.from_meta(meta)
    start = datetime.fromisoformat(meta["slice_start"])
    rows: list[Row] = []
    with gzip.open(stream_dir / "slice.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            local = datetime.fromisoformat(f"{item['дата']}T{item['время']}")
            rows.append(
                Row(
                    offset_s=(local - start).total_seconds(),
                    event_id=item["ид_события"],
                    channel_id=item["ид_канала_данных"],
                    alarm=item["тревожное"],
                    value=item["значение_датчика"],
                )
            )
    rows.sort(key=lambda r: r.offset_s)
    return clock, rows


def as_event(row: Row, at: datetime) -> dict[str, Any]:
    """Строка потока в форме журнала. Время местное московское."""
    local = at.astimezone(UTC) + MOSCOW
    return {
        "ид_события": int(row.event_id) if row.event_id else None,
        "ид_канала_данных": int(row.channel_id),
        "дата": local.strftime("%Y-%m-%d"),
        "время": local.strftime("%H:%M:%S"),
        "тревожное": row.alarm,
        "значение_датчика": row.value,
    }


def first_index(rows: list[Row], offset_s: float) -> int:
    """Первая строка не раньше смещения. Двоичный поиск по отсортированным."""
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid].offset_s < offset_s:
            lo = mid + 1
        else:
            hi = mid
    return lo


class Scenarios:
    """Сценарии пожара поверх недели выгрузки. ADR 0018.

    Кандидатов готовит загрузчик в `fire_scenarios.json`. Заглушка планирует
    сценарии на час вперёд и выпускает их строки в их время. Файла нет значит
    сценариев нет.
    """

    def __init__(self, stream_dir: Path, now: datetime) -> None:
        path = stream_dir / "fire_scenarios.json"
        self.candidates: list[dict[str, Any]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        )
        self.rng = random.Random()
        self.busy: dict[str, datetime] = {}
        self.queue: list[scenarios.Row] = []
        self.planned = _local(now)
        self.next_id = scenarios.EVENT_ID_BASE + self.rng.randrange(10**8)

    def due(self, now: datetime) -> list[dict[str, Any]]:
        """Строки сценариев, время которых наступило."""
        local = _local(now)
        if self.candidates and local + timedelta(minutes=10) >= self.planned:
            end = self.planned + timedelta(hours=1)
            self.queue.extend(
                scenarios.plan(self.candidates, self.planned, end, self.rng, is_day_off, self.busy)
            )
            self.queue.sort(key=lambda r: r.at)
            self.planned = end
        ready = [r for r in self.queue if r.at <= local]
        self.queue = [r for r in self.queue if r.at > local]
        events = []
        for row in ready:
            events.append(row.as_event(self.next_id))
            self.next_id += 1
        return events


def _local(now: datetime) -> datetime:
    return (now.astimezone(UTC) + MOSCOW).replace(tzinfo=None)


def _post(url: str, token: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Stream-Token": token},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def run(stream_dir: Path, api: str | None = None) -> None:
    """Проигрывает отрезок по кругу до сигнала остановки."""
    url = api or os.environ.get("STREAM_API_URL", DEFAULT_API)
    token = os.environ.get("STREAM_TOKEN", "")
    if not token:
        raise SystemExit("заглушке нужен STREAM_TOKEN: без ключа API поток не примет")
    clock, rows = read_slice(stream_dir)
    if not rows:
        raise SystemExit(f"отрезок потока пуст: {stream_dir}")

    running = True

    def _stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _stop)

    loop, offset = clock.position(datetime.now(UTC))
    index = first_index(rows, offset)
    fire = Scenarios(stream_dir, datetime.now(UTC))
    log.info(
        "заглушка СМВУ запущена",
        extra={"rows": len(rows), "loop": loop, "offset_s": int(offset), "url": url},
    )
    pending: list[dict[str, Any]] = []
    sent = 0
    report_at = time.monotonic() + 60
    while running:
        now = datetime.now(UTC)
        while index < len(rows) and clock.moment(loop, rows[index].offset_s) <= now:
            row = rows[index]
            pending.append(as_event(row, clock.moment(loop, row.offset_s)))
            index += 1
            if len(pending) >= BATCH_LIMIT:
                break
        if index >= len(rows):
            loop, index = loop + 1, 0
            log.info("неделя проиграна, следующий круг", extra={"loop": loop})
        pending.extend(fire.due(now))
        if pending:
            try:
                _post(url, token, pending)
                sent += len(pending)
                pending = []
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                log.warning("API не принял пачку, повтор", extra={"error": str(error)})
                pending = pending[-50 * BATCH_LIMIT :]
        if time.monotonic() >= report_at:
            log.info("заглушка СМВУ работает", extra={"sent": sent, "loop": loop})
            report_at = time.monotonic() + 60
        time.sleep(BATCH_SECONDS)
