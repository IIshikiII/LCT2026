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


def applies(predictor: object, conn: Connection, facility_id: str) -> bool:
    """Отвечает, считается ли направление на этом объекте.

    Метод `applies` у предиктора необязателен. Направление без него считается
    на каждом объекте, как раньше. Направление с ним называет свои объекты
    само: модель подтопления училась только на единицах с насосом или датчиком
    затопления, и прогноз на объекте без них был бы числом без смысла.
    """
    check = getattr(predictor, "applies", None)
    return True if check is None else bool(check(conn, facility_id))


def prepare(predictor: object, conn: Connection, at: datetime) -> None:
    """Готовит данные направления один раз перед прогоном по объектам.

    Метод `prepare` у предиктора необязателен. Подтопление размечает в нём
    законченные сутки «вода или проверка», а признаки читают готовую разметку
    (ADR 0013). Считать разметку на каждом объекте было бы повтором одной
    работы сотни раз.
    """
    hook = getattr(predictor, "prepare", None)
    if hook is not None:
        hook(conn, at)


# Границы уровней: код уровня -> нижняя граница вероятности, по возрастанию.
Bands = tuple[tuple[str, float], ...]


def level_bands(
    predictor: object,
    conn: Connection,
    at: datetime,
    fresh: dict[str, tuple[float, FeatureVector]],
) -> Bands | None:
    """Границы уровней на этот прогон. None значит границы реестра направлений.

    Метод `level_bands` у предиктора необязателен. Он получает прогнозы
    текущего прогона, `объект -> (вероятность, признаки)`, до записи в базу.
    Подтопление ставит по ним и по истории скользящий бюджет тревог.
    """
    hook = getattr(predictor, "level_bands", None)
    return None if hook is None else hook(conn, at, fresh)


def own_level(predictor: object, features: FeatureVector, probability: float) -> str | None:
    """Уровень, который назвал сам плагин. None значит уровень по вероятности.

    Метод `level` у предиктора необязателен. Пожарный риск ставит уровень
    экспертными правилами по совпадению признаков (ADR 0016). Его вероятность
    это доля сигнала назавтра на истории, и уровни по ней не упорядочены.
    """
    hook = getattr(predictor, "level", None)
    return None if hook is None else str(hook(features, probability))


def own_summary(predictor: object, features: FeatureVector, probability: float) -> str | None:
    """Строка прогноза от плагина. None значит строка «вероятность N %».

    Метод `summary` у предиктора необязателен. Экспертные правила пишут, какие
    признаки совпали: это диспетчеру полезнее числа.
    """
    hook = getattr(predictor, "summary", None)
    return None if hook is None else str(hook(features, probability))


def incident(predictor: object, features: FeatureVector, at: datetime) -> datetime | None:
    """Начало происшествия, к которому относится прогноз. ADR 0017.

    Метод `incident` у предиктора необязателен. Конвейер держит одну карточку
    на пару «объект и направление» за происшествие: повторный прогон обновляет
    её, а не пишет новую. None значит, что происшествия нет, и ключом служат
    московские сутки. Пожар называет начало эпизода: первый сигнал вне пачки
    в окне 24 часов.
    """
    hook = getattr(predictor, "incident", None)
    return None if hook is None else hook(features, at)


def candidates(predictor: object, conn: Connection, at: datetime) -> set[str] | None:
    """Объекты, которые стоит пересчитать между полными прогонами. ADR 0017.

    Метод `candidates` у предиктора необязателен. Потоковое направление
    считается каждую минуту, но меняется прогноз только там, где пришли
    события или истекло окно. None значит пересчёт всех объектов.
    """
    hook = getattr(predictor, "candidates", None)
    chosen = None if hook is None else hook(conn, at)
    return None if chosen is None else set(chosen)


def live_blocks(predictor: object, conn: Connection, facility_id: str, at: datetime) -> list[Block]:
    """Блоки карточки из свежих данных базы на момент `at`. ADR 0018.

    Метод `live_blocks` у предиктора необязателен. Метод `explain` видит
    только вектор признаков, а графики и хроника нужны по свежим данным потока:
    температура, сигналы по датчикам, работа насоса. Живой блок заменяет блок
    того же типа из `explain`. Пустой ряд плагин в блок не кладёт.
    """
    hook = getattr(predictor, "live_blocks", None)
    return [] if hook is None else list(hook(conn, facility_id, at))


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

    def explain(self, features: FeatureVector, at: datetime | None = None) -> list[Block]:
        """Отдаёт блоки объяснения.

        Конвейер не публикует прогноз без блока `factors` и блока
        `timeseries`. Спецификация §7.

        Аргумент `at` называет момент расчёта признаков. Блоки с осью времени
        ставят на неё абсолютные метки, а вектор признаков держит только
        смещения вида «часов с последней тревоги». Конвейер передаёт `ctx.at`,
        повторный разбор старого прогноза передаёт `prediction.computed_at`.
        Пустое значение значит текущее время. Правило весов блока `factors`
        лежит в `docs/08-ml-plugin.md`.
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
