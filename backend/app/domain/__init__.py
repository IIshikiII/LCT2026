"""Бизнес-логика: действия, переходы статусов, проверка форм."""

from __future__ import annotations

from app.domain.actions import OrderContext, order_actions, prediction_actions
from app.domain.auto_orders import Candidate, create_for, create_missing
from app.domain.transitions import (
    ORDER,
    PREDICTION,
    TRANSITIONS,
    Transition,
    codes_for,
    find,
)
from app.domain.validation import Invalid, validate

__all__ = [
    "ORDER",
    "PREDICTION",
    "TRANSITIONS",
    "Candidate",
    "OrderContext",
    "Invalid",
    "Transition",
    "create_for",
    "create_missing",
    "codes_for",
    "find",
    "order_actions",
    "prediction_actions",
    "validate",
]
