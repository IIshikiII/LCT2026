"""Общие части роутеров: время, сортировка, страницы, разбор bbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Column
from sqlalchemy.sql.elements import UnaryExpression

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def iso(value: datetime | None) -> str:
    """Метка времени в виде `2026-09-09T12:00:00Z`. Фронт ждёт именно такую."""
    if value is None:
        return ""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def day_start(value: str) -> datetime:
    """Начало дня в UTC. Колонки времени сравниваются со временем, не со строкой."""
    return datetime.fromisoformat(value[:10]).replace(tzinfo=UTC)


def day_end(value: str) -> datetime:
    """Конец дня включительно.

    Фильтр по одной дате обязан вернуть всё, что посчитано в этот день.
    """
    day = datetime.fromisoformat(value[:10]).replace(tzinfo=UTC)
    return day.replace(hour=23, minute=59, second=59, microsecond=999999)


def order_by(
    sort: str | None,
    allowed: dict[str, Column[object]],
    fallback: UnaryExpression[object],
) -> UnaryExpression[object]:
    """Разбирает `field:asc` или `field:desc` по белому списку.

    Неизвестное поле — не ошибка. Фронт получает список в порядке по
    умолчанию, а не сообщение об ошибке.
    """
    if not sort:
        return fallback
    field, _, direction = sort.partition(":")
    column = allowed.get(field)
    if column is None:
        return fallback
    return column.desc() if direction == "desc" else column.asc()


@dataclass(frozen=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


def parse_bbox(raw: str | None) -> BBox | None:
    """Разбирает `minLon,minLat,maxLon,maxLat`.

    Битое значение — не ошибка. Карта получает всё, а не пустой экран.
    """
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError:
        return None
    return BBox(min_lon, min_lat, max_lon, max_lat)


def clamp_page(page: int, page_size: int) -> tuple[int, int]:
    return max(page, 1), min(max(page_size, 1), MAX_PAGE_SIZE)
