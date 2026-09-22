"""Реестр признаков: имя и способ его посчитать.

## Зачем

Раньше `build_features` отдавал жёсткий словарь из семнадцати ключей, а модель
требовала свой список через `model.feature_name()`. Пока списки совпадали,
всё работало. Как только обучили модель на другом наборе, замена файла модели
перестала быть заменой файла: надо было переписывать модуль признаков.

Реестр разрывает эту связь. Модель называет признаки, реестр их считает,
конвейер ничего о них не знает. Новая модель на уже известных признаках
подключается копированием файла.

## Правила

1. **Ключ реестра — это имя признака в модели.** Буква в букву, иначе модель
   получит не тот столбец и промолчит.
2. **Расхождение ловится при загрузке, а не при прогнозе.** Модель просит
   признак, которого в реестре нет, значит направление отказывает сразу и
   называет недостающие имена.
3. **Ни один построитель не смотрит после `at`.** Это правило проверяет тест
   `tests/test_features_no_leak.py`.

## Как добавить признак

Положить функцию в модуль направления и записать её в его реестр. Больше
ничего: ни конвейер, ни схема, ни фронтенд не меняются.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.engine import Connection

from app.ml.protocol import FeatureVector

# Построитель одного признака. Три довода: соединение, объект, момент расчёта.
Builder = Callable[[Connection, str, datetime], float]

# Реестры по направлениям: код направления -> имя признака -> построитель.
_REGISTRY: dict[str, dict[str, Builder]] = {}


def register(direction: str, builders: dict[str, Builder]) -> None:
    """Добавляет построители направления. Повтор имени — ошибка сборки."""
    target = _REGISTRY.setdefault(direction, {})
    for name, builder in builders.items():
        if name in target:
            raise ValueError(f"признак {name} направления {direction} уже зарегистрирован")
        target[name] = builder


def known(direction: str) -> frozenset[str]:
    """Имена, которые направление умеет считать."""
    return frozenset(_REGISTRY.get(direction, {}))


def missing(direction: str, names: list[str]) -> list[str]:
    """Имена, которых реестр не знает. Пустой список значит модель подходит."""
    have = known(direction)
    return [name for name in names if name not in have]


def require(direction: str, names: list[str]) -> None:
    """Проверяет, что направление умеет посчитать все признаки модели.

    Отказ тут стоит одной строки в журнале при загрузке. Отказ при прогнозе
    стоил бы `KeyError` на каждом объекте и пустого направления без объяснения.
    """
    gap = missing(direction, names)
    if gap:
        raise LookupError(
            f"модель направления {direction} просит признаки, которых нет в реестре: "
            f"{', '.join(gap)}. Добавьте построители или поставьте другую модель."
        )


def build(
    direction: str, conn: Connection, facility_id: str, at: datetime, names: list[str]
) -> FeatureVector:
    """Считает ровно те признаки, которые назвала модель, и в её порядке."""
    require(direction, names)
    builders = _REGISTRY[direction]
    return {name: float(builders[name](conn, facility_id, at)) for name in names}
