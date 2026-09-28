"""Ссылка на открытую заявку объекта.

Автозаявка на объект и направление одна, пока она открыта. Свежий прогноз того
же объекта своей заявки не получает, и карточка ссылается на ту, что есть.
Иначе высокий риск без заявки выглядит как сбой.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import engine
from app.main import API_PREFIX, app
from app.tables import work_order

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)


def get(path: str) -> Any:
    response = client.get(f"{API_PREFIX}{path}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_prediction_without_its_own_order_points_to_the_open_one() -> None:
    """P-2 лежит на объекте P-1, у которого открыта заявка O-1."""
    body = get("/predictions/P-2")
    assert body["orderId"] is None
    assert body["facilityOrderId"] == "O-1"


def test_an_own_order_hides_the_facility_order() -> None:
    body = get("/predictions/P-1")
    assert body["orderId"] == "O-1"
    assert body["facilityOrderId"] is None


def test_a_closed_order_is_not_offered() -> None:
    with engine().begin() as conn:
        conn.execute(update(work_order).where(work_order.c.id == "O-1").values(status="REJECTED"))

    assert get("/predictions/P-2")["facilityOrderId"] is None


def test_the_journal_carries_the_link_too() -> None:
    rows = {item["id"]: item for item in get("/predictions?pageSize=50")["items"]}
    assert rows["P-2"]["facilityOrderId"] == "O-1"
