"""DTO прогнозов, заявок, метрик и дашборда.

Поля `direction`, `level` и `status` — обычные строки. Enum здесь запрещён
спецификацией §5: пятое направление обязано появиться без правки схем.
"""

from __future__ import annotations

from typing import Any

from app.schemas.base import Dto


class FacilityRef(Dto):
    id: str
    collector: str
    section: str | None = None
    chamber: str | None = None
    device: str | None = None
    district: str
    address: str
    lat: float
    lon: float


class CardBlock(Dto):
    """Блок объяснения прогноза.

    Форму `data` знает только компонент блока во фронте. Неизвестный `type`
    фронт рисует запасным компонентом и не падает.
    """

    type: str
    title: str
    data: Any = None


class FieldCondition(Dto):
    """Условие доступности поля: значение другого поля формы не равно `not_equals`."""

    field: str
    not_equals: str


class FieldDef(Dto):
    name: str
    label: str
    type: str
    required: bool | None = None
    min_length: int | None = None
    options_ref: str | None = None
    placeholder: str | None = None
    help: str | None = None
    # Значение, с которым поле открывается. Сервер знает его, фронт нет:
    # например, уровень модели в форме решения диспетчера.
    default: str | None = None
    # Поле доступно, только пока условие выполняется. Иначе оно заблокировано
    # и не уходит на сервер.
    enabled_when: FieldCondition | None = None


class ActionDef(Dto):
    code: str
    label: str
    kind: str
    confirm: str | None = None
    # Подсказка под кнопкой. Несёт контекст, который знает только сервер:
    # например, за кем сейчас закреплён прогноз.
    help: str | None = None
    fields: list[FieldDef] = []


class Prediction(Dto):
    id: str
    direction: str
    level: str
    probability: float
    horizon_hours: int
    computed_at: str
    compute_ms: int
    status: str
    facility: FacilityRef
    summary: str
    order_id: str | None = None
    # Статус своей заявки: плашка карточки показывает, что с ней сейчас.
    order_status: str | None = None
    # Открытая заявка того же объекта и направления от другого прогноза.
    # Приходит, только когда своей заявки нет: автозаявка на объект одна.
    facility_order_id: str | None = None
    # Решение диспетчера. Журнал показывает его отдельной колонкой, потому что
    # выгрузка ТЗ §10 требует «результаты отработки» рядом с прогнозом. ADR 0006.
    assignee: str | None = None
    verdict: str | None = None
    dispatcher_level: str | None = None
    decided_at: str | None = None
    # Итог бригады, сведённый в одну строку: подтверждён факт или нет. Полный
    # исход с причиной и комментарием лежит в заявке.
    fact_confirmed: bool | None = None


class PredictionDetail(Prediction):
    blocks: list[CardBlock] = []
    actions: list[ActionDef] = []


class WorkOrderOutcome(Dto):
    """Итог работ на объекте. ADR 0006.

    Поле называется `factConfirmed`, а не `predictionConfirmed`: бригада видела
    факт, а не пользу выезда. Старое имя смешивало две разные величины, и
    `docs/06-labels-and-metrics.md` это запрещает.
    """

    actual_cause: str
    fact_confirmed: bool
    comment: str
    closed_at: str


class WorkOrder(Dto):
    id: str
    number: str
    prediction_id: str
    facility: FacilityRef
    work_type: str
    due_at: str
    status: str
    # `PIPELINE` или `DISPATCHER`. Происхождение, а не состояние: обе заявки
    # живут одинаково, различает их только то, кто их породил. ADR 0006.
    created_by: str = "PIPELINE"
    created_at: str
    actions: list[ActionDef] = []
    outcome: WorkOrderOutcome | None = None


class ModelMetric(Dto):
    """Качество направления.

    Пустые `precision` и `recall` значат «точность не измерена». Поле `note`
    тогда говорит почему, а `method` называет способ работы направления:
    `offline_holdout` у модели, `expert_rules` у экспертных правил (ADR 0016).
    """

    direction: str
    precision: float | None = None
    recall: float | None = None
    target_precision: float
    target_recall: float
    evaluated_at: str
    method: str | None = None
    note: str | None = None
    # Наивное правило на той же выборке: что это за правило и его числа.
    baseline_rule: str | None = None
    baseline_precision: float | None = None
    baseline_recall: float | None = None


class PipelineHealth(Dto):
    last_run_at: str
    last_run_ms: int
    freshness_minutes: float
    max_compute_ms: int
    min_horizon_hours: int
    target_compute_ms: int
    target_horizon_hours: int
    # Задержка потока последнего прогона: от метки события до конца прогона.
    # Пусто, если за прогон не пришло ни одного события потока. ADR 0017.
    stream_lag_ms: int | None = None
    stream_events: int = 0
    target_stream_lag_ms: int = 300_000


class AlertFilter(Dto):
    """Фильтр журнала, который показывает инциденты уведомления."""

    level: list[str]
    status: list[str]


class Alert(Dto):
    """Уведомление о тревоге. ТЗ §10, ADR 0019.

    Текст и фильтр готовит сервер: фронт не знает, какой уровень и какой
    статус тревожны. `repeat_minutes` задаёт шаг, с которым интерфейс
    напоминает, пока тревога жива.
    """

    code: str
    level: str
    count: int
    title: str
    hint: str
    filter: AlertFilter
    repeat_minutes: int
    newest_at: str | None = None


class DashboardSummary(Dto):
    by_level: dict[str, int]
    by_direction: dict[str, int]
    by_status: dict[str, int]
    by_order_status: dict[str, int]
    total: int


class SeriesPoint(Dto):
    t: str
    v: float


class Series(Dto):
    name: str
    unit: str | None = None
    points: list[SeriesPoint] = []


class TimeSeriesResponse(Dto):
    series: list[Series] = []
    marker_at: str | None = None
