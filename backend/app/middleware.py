"""Промежуточный слой запроса: идентификатор и строка лога на каждый вызов."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app import request_context

log = logging.getLogger("app.access")

Handler = Callable[[Request], Awaitable[Response]]


async def _with_request_id(request: Request, call_next: Handler) -> Response:
    """Ставит идентификатор запроса и пишет строку доступа.

    Идентификатор берётся из заголовка, если клиент его прислал. Так запрос
    прослеживается через nginx и через фронт. Иначе сервер создаёт свой.
    """
    request_id = request.headers.get(request_context.HEADER) or uuid.uuid4().hex
    request_context.set_request_id(request_id)

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)

    response.headers[request_context.HEADER] = request_id
    log.info(
        "запрос обработан",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "durationMs": duration_ms,
        },
    )
    return response


def install(app: FastAPI) -> None:
    app.middleware("http")(_with_request_id)
