"""DTO ответов API. Зеркало `frontend/src/shared/api/schemas.ts` поле в поле.

Переименование поля здесь ломает фронт. Правка идёт вместе с правкой
`schemas.ts` и мок-хендлера в том же коммите.
"""

from __future__ import annotations

from app.schemas.base import Dto, Page
from app.schemas.core import (
    ActionDef,
    CardBlock,
    DashboardSummary,
    FacilityRef,
    FieldDef,
    ModelMetric,
    PipelineHealth,
    Prediction,
    PredictionDetail,
    Series,
    SeriesPoint,
    TimeSeriesResponse,
    WorkOrder,
    WorkOrderOutcome,
)
from app.schemas.meta import (
    AppMeta,
    DirectionMeta,
    DistrictMeta,
    ReasonOption,
    RiskLevelMeta,
    StatusMeta,
)

__all__ = [
    "ActionDef",
    "AppMeta",
    "CardBlock",
    "DashboardSummary",
    "DirectionMeta",
    "DistrictMeta",
    "Dto",
    "FacilityRef",
    "FieldDef",
    "ModelMetric",
    "Page",
    "PipelineHealth",
    "Prediction",
    "PredictionDetail",
    "ReasonOption",
    "RiskLevelMeta",
    "Series",
    "SeriesPoint",
    "StatusMeta",
    "TimeSeriesResponse",
    "WorkOrder",
    "WorkOrderOutcome",
]
