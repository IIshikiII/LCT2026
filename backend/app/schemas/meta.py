"""DTO для GET /meta. Зеркало AppMetaSchema во фронте."""

from __future__ import annotations

from app.schemas.base import Dto


class DirectionMeta(Dto):
    code: str
    label: str
    short_label: str
    accent: str
    min_horizon_hours: int


class RiskLevelMeta(Dto):
    code: str
    label: str
    color_var: str
    order: int


class StatusMeta(Dto):
    code: str
    label: str
    scope: str
    color_var: str | None = None
    terminal: bool | None = None


class DistrictMeta(Dto):
    code: str
    label: str


class ReasonOption(Dto):
    code: str
    label: str


class AppMeta(Dto):
    directions: list[DirectionMeta]
    risk_levels: list[RiskLevelMeta]
    statuses: list[StatusMeta]
    districts: list[DistrictMeta]
    journal_columns: list[str]
    order_columns: list[str]
    dashboard_widgets: list[str]
    reasons: dict[str, list[ReasonOption]]
