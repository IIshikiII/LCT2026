"""Реестр предикторов. Спецификация §7.

Добавить направление в конвейер — значит положить файл в `app/ml/plugins/` и
добавить запись в `app/meta/directions.py`. Ни конвейер, ни роутеры, ни схемы
при этом не меняются.

Модуль импортирует scikit-learn только через плагины, поэтому образ `api` его
не тянет. Ни один модуль под `app/api/` не имеет права импортировать `app.ml`.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from app.meta import active as active_directions
from app.ml.protocol import Predictor

log = logging.getLogger(__name__)

_PREDICTORS: dict[str, Predictor] = {}
_LOADED = False


def register(predictor: Predictor) -> None:
    """Ставит предиктор в реестр.

    Два предиктора на одно направление — ошибка сборки, а не выбор во время
    работы. Молчаливая замена дала бы разный результат от порядка импорта.
    """
    code = predictor.code
    if code in _PREDICTORS:
        raise ValueError(f"направление {code} уже имеет предиктор")
    _PREDICTORS[code] = predictor


def load_plugins() -> None:
    """Импортирует все модули из `app/ml/plugins/`. Каждый регистрирует себя."""
    global _LOADED
    if _LOADED:
        return

    import app.ml.plugins as package

    for module in pkgutil.iter_modules(package.__path__):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{package.__name__}.{module.name}")
    _LOADED = True


def get(code: str) -> Predictor | None:
    """Отдаёт предиктор направления. Отдаёт None, когда предиктора нет."""
    load_plugins()
    return _PREDICTORS.get(code)


def active() -> tuple[Predictor, ...]:
    """Отдаёт предикторы включённых направлений, в порядке реестра направлений.

    Направление без предиктора пропускается молча: это штатное состояние до
    того, как команда обучит модель. Спецификация §13.
    """
    load_plugins()
    found = (_PREDICTORS.get(item.code) for item in active_directions())
    return tuple(item for item in found if item is not None)


def missing() -> tuple[str, ...]:
    """Отдаёт коды включённых направлений, у которых предиктора нет.

    Прогон конвейера пишет этот список в лог. Пустой результат по направлению
    обязан быть видимым решением, а не тихой пропажей.
    """
    load_plugins()
    return tuple(item.code for item in active_directions() if item.code not in _PREDICTORS)


def reset() -> None:
    """Чистит реестр. Только для тестов."""
    global _LOADED
    _PREDICTORS.clear()
    _LOADED = False
