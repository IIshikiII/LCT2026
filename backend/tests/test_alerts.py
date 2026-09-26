"""Уведомления о критических инцидентах. ТЗ §10, ADR 0019.

Фикстура `seeded` держит один критический прогноз в статусе «Новый» (P-1) и
один закрытый (P-5). Тревога живёт, пока новый критический прогноз никто не
взял в работу.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.alerts import _plural
from app.db import engine
from app.main import API_PREFIX, app
from app.tables import prediction

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)


def alerts() -> Any:
    response = client.get(f"{API_PREFIX}/alerts")
    assert response.status_code == 200, response.text
    return response.json()


def test_an_untaken_critical_incident_raises_an_alert() -> None:
    body = alerts()
    assert len(body) == 1
    alert = body[0]
    assert alert["count"] == 1
    assert alert["title"] == "1 критический инцидент не взят в работу"
    assert alert["filter"] == {"level": ["CRITICAL"], "status": ["NEW"]}
    assert alert["repeatMinutes"] == 5


def test_the_alert_link_shows_exactly_its_incidents() -> None:
    link = alerts()[0]["filter"]
    response = client.get(f"{API_PREFIX}/predictions", params=link)
    assert [item["id"] for item in response.json()["items"]] == ["P-1"]


def test_the_alert_goes_away_when_the_incident_is_taken() -> None:
    with engine().begin() as conn:
        conn.execute(
            prediction.update()
            .where(prediction.c.id == "P-1")
            .values(status="IN_REVIEW", assignee="ods")
        )
    assert alerts() == []


@pytest.mark.parametrize(
    ("n", "text"),
    [
        (1, "1 критический инцидент не взят в работу"),
        (3, "3 критических инцидента не взяты в работу"),
        (11, "11 критических инцидентов не взяты в работу"),
        (21, "21 критический инцидент не взят в работу"),
        (25, "25 критических инцидентов не взяты в работу"),
    ],
)
def test_the_title_agrees_with_the_number(n: int, text: str) -> None:
    assert _plural(n) == text
