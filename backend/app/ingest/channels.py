"""Разбор названия канала: пикет, смещение и галерея. Пикет равен 10 метрам и
является контейнером оборудования, так его определил заказчик во время
QA-сессии.

Правило повторяет `ml/common/prepare_inputs.py`, функция `parse_picket`.
По нему построены единицы всех витрин `ml/`, поэтому расхождение здесь сдвинет
единицу прогноза.

Формы названия:

| Название | Пикет | Смещение, м | Галерея |
|---|---|---|---|
| `ТД ПК87` | 87 | 0 | основная линия |
| `ДД ПК1+0,5` | 1 | 0,5 | основная линия |
| `ТД ПК290-292` | 290 | 0 | основная линия |
| `Темп. ВШ ПК88,5` | 88 | 0 | основная линия |
| `Темп. ПК399 Г0ПК5` | 5 | 0 | галерея 0 |

Основная линия несёт ключ галереи −1. Галерея входит в ключ единицы: пикет 5
основной линии и пикет 5 галереи 1 это разные места.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAIN_LINE = -1
PICKET_M = 10.0

_PK = re.compile(r"ПК\s?(\d+)(?:\s?[Гг]\s?(\d+)\s?ПК\s?(\d+))?(?:\s?\+\s?(\d+(?:[.,]\d+)?))?")


@dataclass(frozen=True)
class Place:
    """Место канала на линии объекта."""

    picket: int
    offset_m: float
    gallery: int

    @property
    def chainage_m(self) -> float:
        """Расстояние от начала линии: пикет равен 10 метрам."""
        return self.picket * PICKET_M + self.offset_m


def parse_place(name: str | None) -> Place | None:
    """Место канала по названию. Пусто, если в названии нет пикета."""
    match = _PK.search(name or "")
    if match is None:
        return None
    pk, gallery, gallery_pk, offset = match.groups()
    if gallery is not None:
        return Place(int(gallery_pk), 0.0, int(gallery))
    shift = float(offset.replace(",", ".")) if offset else 0.0
    return Place(int(pk), shift, MAIN_LINE)
