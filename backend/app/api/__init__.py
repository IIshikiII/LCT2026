"""Роутеры API. По одному на область экрана.

Все монтируются под `/api/v1`. Добавить область — значит добавить модуль и
строку в `ROUTERS`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import dashboard, facilities, meta, metrics, orders, predictions

ROUTERS: tuple[APIRouter, ...] = (
    meta.router,
    predictions.router,
    facilities.router,
    orders.router,
    metrics.router,
    dashboard.router,
)

__all__ = ["ROUTERS"]
