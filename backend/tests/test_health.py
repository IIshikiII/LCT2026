from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app


class FakeConn:
    def execute(self, statement: object) -> None:
        return None


def fake_conn() -> Iterator[FakeConn]:
    yield FakeConn()


def test_healthz_answers_ok() -> None:
    app.dependency_overrides[get_conn] = fake_conn
    try:
        response = TestClient(app).get("/healthz")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
