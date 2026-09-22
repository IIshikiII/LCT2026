"""Роутер GET /meta."""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.deps import CurrentActor
from app.meta import build_meta
from app.schemas import AppMeta

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=AppMeta, response_model_by_alias=True)
def get_meta(actor: CurrentActor) -> AppMeta:
    """Справочники интерфейса.

    Состав справочников от роли не зависит: техник видит меньше строк, но те же
    уровни риска и те же направления. Токен здесь нужен затем, чтобы состав
    экранов не читался до входа.
    """
    del actor
    return build_meta()
