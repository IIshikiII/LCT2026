"""Роутер GET /meta."""

from __future__ import annotations

from fastapi import APIRouter

from app.meta import build_meta
from app.schemas import AppMeta

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=AppMeta, response_model_by_alias=True)
def get_meta() -> AppMeta:
    return build_meta()
