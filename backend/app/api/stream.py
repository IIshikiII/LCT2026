"""Приём потока СМВУ. ADR 0017.

Поток шлёт система, а не человек. Токен сессии диспетчера ей не положен,
поэтому ручка проверяет свой ключ `STREAM_TOKEN` в заголовке
`X-Stream-Token`. Пустой ключ выключает ручку: она отвечает 404, как будто её
нет. Путь стоит в `PUBLIC_PATHS`, потому что зависимость `current_actor` к
системе неприменима.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.engine import Connection

from app.config import config
from app.db import get_tx
from app.ingest.stream import accept, parse_row
from app.schemas.stream import StreamAccepted, StreamBatch

router = APIRouter(tags=["stream"], prefix="/stream")

Writer = Annotated[Connection, Depends(get_tx)]


def _check(token: str | None) -> None:
    if not config.stream_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="приём потока выключен")
    if token is None or not hmac.compare_digest(token, config.stream_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="неверный ключ потока")


@router.post("/events", response_model=StreamAccepted, response_model_by_alias=True)
def post_events(
    batch: StreamBatch,
    conn: Writer,
    x_stream_token: Annotated[str | None, Header()] = None,
) -> StreamAccepted:
    """Принимает пачку строк журнала и пишет события и показания."""
    _check(x_stream_token)
    rows = [
        parse_row(e.event_id, e.channel_id, e.day, e.clock, e.alarm, e.value) for e in batch.events
    ]
    result = accept(conn, rows, datetime.now(UTC))
    return StreamAccepted(
        received=result.received,
        events=result.events,
        readings=result.readings,
        skipped=result.skipped,
    )
