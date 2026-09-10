"""Роутер области `predictions`. Эндпоинты приходят с задачей 6."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["predictions"])
