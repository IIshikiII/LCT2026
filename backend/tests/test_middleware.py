"""Идентификатор запроса, формат ошибки и префикс API."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient

from app import request_context
from app.logging import JsonFormatter
from app.main import API_PREFIX, app

client = TestClient(app)

probe = APIRouter()


@probe.get("/probe/boom")
def _boom() -> None:
    raise RuntimeError("тестовый сбой")


@probe.get("/probe/missing")
def _missing() -> None:
    raise HTTPException(status_code=404, detail="объект X не найден")


app.include_router(probe, prefix=API_PREFIX)


def test_meta_lives_under_the_api_prefix() -> None:
    assert client.get(f"{API_PREFIX}/meta").status_code == 200
    assert client.get("/meta").status_code == 404


def test_response_carries_a_request_id() -> None:
    response = client.get(f"{API_PREFIX}/meta")
    assert response.headers[request_context.HEADER]


def test_the_client_request_id_survives() -> None:
    # Так запрос прослеживается через nginx и через фронт.
    response = client.get(f"{API_PREFIX}/meta", headers={request_context.HEADER: "abc123"})
    assert response.headers[request_context.HEADER] == "abc123"


def test_http_error_answers_with_a_detail_body() -> None:
    response = client.get(f"{API_PREFIX}/probe/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "объект X не найден"}


def test_unhandled_error_answers_500_without_the_traceback() -> None:
    silent = TestClient(app, raise_server_exceptions=False)
    response = silent.get(f"{API_PREFIX}/probe/boom")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "тестовый сбой" not in detail
    assert "requestId" in detail


def test_every_log_line_carries_the_request_id() -> None:
    request_context.set_request_id("r-42")
    record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "проверка", None, None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["requestId"] == "r-42"


@pytest.mark.parametrize(
    "area", ["meta", "predictions", "facilities", "orders", "metrics", "dashboard"]
)
def test_every_area_router_is_mounted(area: str) -> None:
    from app.api import ROUTERS

    tags = {tag for router in ROUTERS for tag in (router.tags or [])}
    assert area in tags
