"""Идентификатор запроса. Живёт в контексте задачи и попадает в каждый лог."""

from __future__ import annotations

from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

HEADER = "X-Request-Id"


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str:
    return _request_id.get()
