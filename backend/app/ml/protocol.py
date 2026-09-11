"""Протокол плагина направления. Спецификация §7.

Плагин отвечает за четыре вопроса, у каждого направления свои: какие признаки
построить, какова вероятность, чем её объяснить и какое событие считать
наступившим. Всё остальное о направлении — подпись, горизонт, типы работ,
пороги заявки — держит реестр `app/meta/directions.py`. Плагин знает только
свой код и берёт остальное оттуда.

Причина разделения: подпись направления нужна API, который не видит ни одной
модели. Копия подписи в плагине разошлась бы с реестром на первой же правке.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.engine import Connection

# Вход модели: имя признака -> число. Этот словарь конвейер кладёт в
# `prediction.features` как есть, поэтому имена признаков обязаны быть
# устойчивыми: по ним разбирают прогноз через полгода.
FeatureVector = dict[str, float]


@dataclass(frozen=True)
class Window:
    """Окно времени. Обе границы включены."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class FeatureContext:
    """Всё, что плагин имеет право прочитать при построении признаков.

    Поле `at` — момент расчёта. Брать данные с меткой времени после него
    запрещено: это утечка, которая поднимает метрики на отложенной выборке и
    роняет модель в работе. Спецификация §7.
    """

    conn: Connection
    facility_id: str
    at: datetime


@dataclass(frozen=True)
class Block:
    """Блок объяснения для карточки диспетчера.

    Совпадает по форме с `CardBlock` в `app/schemas/core.py`. Типы, которые
    фронт рисует своими компонентами: `factors`, `timeseries`, `timeline`,
    `table`, `keyvalue`. Любой другой тип фронт рисует запасным компонентом.
    """

    type: str
    title: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "title": self.title, "data": self.data}


@runtime_checkable
class Predictor(Protocol):
    """Предиктор одного направления.

    Реализация живёт в `app/ml/plugins/<код>.py` и регистрирует себя вызовом
    `app.ml.registry.register`. Направление без предиктора — законное
    состояние: прогнозов по нему просто нет.
    """

    code: str

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        """Строит признаки объекта на момент `ctx.at`."""
        ...

    def predict(self, features: FeatureVector) -> float:
        """Отдаёт вероятность события в окне горизонта. Значение от 0 до 1."""
        ...

    def explain(self, features: FeatureVector) -> list[Block]:
        """Отдаёт блоки объяснения.

        Конвейер не публикует прогноз без блока `factors` и блока
        `timeseries`. Спецификация §7.
        """
        ...

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        """Называет тип работ для автозаявки.

        Значение обязано входить в `Direction.work_types` этого направления.
        Реестр держит список, потому что тот же список видит диспетчер.
        """
        ...

    def label_rule(self, conn: Connection, facility_id: str, window: Window) -> bool:
        """Отвечает, наступило ли событие направления на объекте в окне.

        Метод нужен обучению, а не прогнозу. Событие у каждого направления
        своё и лежит в своей таблице, поэтому общего правила нет. Точные
        правила пишутся после первого просмотра выгрузок, см.
        `docs/06-labels-and-metrics.md`.
        """
        ...
