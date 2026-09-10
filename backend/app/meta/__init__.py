"""Реестры предметной области. Из них строится `/meta` и вся композиция экрана."""

from __future__ import annotations

from app.meta.build import build_meta
from app.meta.catalog import (
    DASHBOARD_WIDGETS,
    DISTRICTS,
    JOURNAL_COLUMNS,
    ORDER_COLUMNS,
    ORDER_SCOPE,
    PREDICTION_SCOPE,
    REJECTION_REASONS,
    REJECTION_REASONS_REF,
    RISK_LEVELS,
    STATUSES,
    is_terminal,
    level_for,
    level_order,
    statuses_for,
)
from app.meta.directions import REGISTRY, Direction, Reason, active, by_code

__all__ = [
    "DASHBOARD_WIDGETS",
    "DISTRICTS",
    "JOURNAL_COLUMNS",
    "ORDER_COLUMNS",
    "ORDER_SCOPE",
    "PREDICTION_SCOPE",
    "REGISTRY",
    "REJECTION_REASONS",
    "REJECTION_REASONS_REF",
    "RISK_LEVELS",
    "STATUSES",
    "Direction",
    "Reason",
    "active",
    "build_meta",
    "by_code",
    "is_terminal",
    "level_for",
    "level_order",
    "statuses_for",
]
