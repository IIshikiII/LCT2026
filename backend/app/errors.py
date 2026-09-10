"""Формат ошибок. Тело ответа всегда `{"detail": ...}`.

Фронт тело не читает: любой код от 400 и выше он показывает как ошибку с
кнопкой повтора. Текст нужен человеку при отладке, поэтому он должен быть
осмысленным.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def not_found(entity: str, entity_id: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"{entity} {entity_id} не найден"})


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Последний рубеж.

    Трассировка уходит в лог вместе с `requestId`. Клиенту уходит только код
    ошибки: внутренности сервиса наружу не отдаём.
    """
    log.exception("необработанная ошибка", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"detail": "внутренняя ошибка сервиса, см. лог по requestId"},
    )


def install(app: FastAPI) -> None:
    app.add_exception_handler(Exception, _unhandled)
