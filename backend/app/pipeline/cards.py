"""Сборка карточки прогноза: живые блоки, свежесть, демонстрационная шкала.

ADR 0018. Модуль не читает базу сам: он соединяет то, что отдали плагины.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ml.protocol import Block

MOSCOW = timedelta(hours=3)

# Блоки этих типов живой блок заменяет. Факторы и пояснения остаются от
# `explain`: они описывают расчёт вероятности, а не свежие данные.
REPLACEABLE = frozenset({"timeseries", "timeline", "table"})
FRESHNESS_TITLE = "Свежесть прогноза"

# Демонстрационная шкала уровней: доля объектов направления от опасного к
# спокойному. Оставшиеся объекты получают LOW. ADR 0018.
DEMO_SHARES: tuple[tuple[str, float, int], ...] = (
    ("CRITICAL", 0.02, 1),
    ("HIGH", 0.06, 2),
    ("MEDIUM", 0.17, 3),
)


def merge(static: list[dict[str, Any]], live: list[Block]) -> list[dict[str, Any]]:
    """Кладёт живые блоки поверх блоков расчёта.

    Живой блок заменяет первый блок того же типа из `REPLACEABLE` или блок с
    тем же заголовком. Остальные живые блоки встают в конец.
    """
    result = [dict(b) for b in static]
    for block in live:
        fresh = block.as_dict()
        index = next(
            (
                i
                for i, old in enumerate(result)
                if old.get("title") == fresh["title"]
                or (old.get("type") == fresh["type"] and fresh["type"] in REPLACEABLE)
            ),
            None,
        )
        if index is None:
            result.append(fresh)
        else:
            result[index] = fresh
    return result


def _clock(at: datetime) -> str:
    return (at.astimezone(UTC) + MOSCOW).strftime("%H:%M")


def freshness(point: datetime, at: datetime) -> Block:
    """Когда посчитана вероятность и когда обновлены графики суточной карточки."""
    return Block(
        type="keyvalue",
        title=FRESHNESS_TITLE,
        data={
            "items": [
                {"label": "Вероятность", "value": f"на сутки от {_clock(point)}"},
                {"label": "Графики обновлены", "value": _clock(at)},
            ]
        },
    )


def rank_levels(probabilities: dict[str, float]) -> dict[str, str]:
    """Уровни по рангу вероятности: 2 % критических, 6 % высоких, 17 % средних.

    Самые вероятные объекты направления получают верхние уровни. Каждый уровень
    берёт не меньше своего минимума, поэтому на малом числе объектов порядок
    «критических меньше высоких, высоких меньше средних» сохраняется.
    """
    ranked = sorted(probabilities, key=lambda f: (-_value(probabilities[f]), f))
    levels: dict[str, str] = {}
    start = 0
    for code, share, least in DEMO_SHARES:
        count = max(least, round(share * len(ranked)))
        for fid in ranked[start : start + count]:
            levels[fid] = code
        start += count
    for fid in ranked[start:]:
        levels[fid] = "LOW"
    return levels


def _value(p: float) -> float:
    return -1.0 if math.isnan(p) else p
