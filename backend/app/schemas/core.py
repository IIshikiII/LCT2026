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


class FieldDef(Dto):
    name: str
    label: str
    type: str
    required: bool | None = None
    min_length: int | None = None
    options_ref: str | None = None
    placeholder: str | None = None
    help: str | None = None


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
    created_at: str
    actions: list[ActionDef] = []
    outcome: WorkOrderOutcome | None = None


class ModelMetric(Dto):
    direction: str
    precision: float
    recall: float
    target_precision: float
    target_recall: float
    evaluated_at: str


class PipelineHealth(Dto):
    last_run_at: str
    last_run_ms: int
    freshness_minutes: float
    max_compute_ms: int
    min_horizon_hours: int
    target_compute_ms: int
    target_horizon_hours: int


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
