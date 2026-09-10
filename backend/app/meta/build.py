"""Сборка ответа GET /meta из реестров."""

from __future__ import annotations

from app.meta import catalog, directions
from app.schemas import (
    AppMeta,
    DirectionMeta,
    DistrictMeta,
    ReasonOption,
    RiskLevelMeta,
    StatusMeta,
)


def build_meta() -> AppMeta:
    active = directions.active()

    reasons: dict[str, list[ReasonOption]] = {
        catalog.REJECTION_REASONS_REF: [
            ReasonOption(code=item.code, label=item.label) for item in catalog.REJECTION_REASONS
        ]
    }
    for direction in active:
        reasons[direction.reasons_ref] = [
            ReasonOption(code=item.code, label=item.label) for item in direction.reasons
        ]

    return AppMeta(
        directions=[
            DirectionMeta(
                code=item.code,
                label=item.label,
                short_label=item.short_label,
                accent=item.accent,
                min_horizon_hours=item.min_horizon_hours,
            )
            for item in active
        ],
        risk_levels=[
            RiskLevelMeta(
                code=item.code,
                label=item.label,
                color_var=item.color_var,
                order=item.order,
            )
            for item in catalog.RISK_LEVELS
        ],
        statuses=[
            StatusMeta(
                code=item.code,
                label=item.label,
                scope=item.scope,
                color_var=item.color_var,
                terminal=item.terminal or None,
            )
            for item in catalog.STATUSES
        ],
        districts=[DistrictMeta(code=item.code, label=item.label) for item in catalog.DISTRICTS],
        journal_columns=list(catalog.JOURNAL_COLUMNS),
        order_columns=list(catalog.ORDER_COLUMNS),
        dashboard_widgets=list(catalog.DASHBOARD_WIDGETS),
        reasons=reasons,
    )
