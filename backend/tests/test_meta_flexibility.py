"""Тест на гибкость, зеркальный фронтовому.

Пятое направление обязано появиться в `/meta` от одной записи в реестре и
одной переменной окружения. Ни один роутер, ни одна схема и ни одна миграция
при этом не меняются.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def five_directions(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Поднимает приложение с включённым пятым направлением."""
    codes = "SENSOR_FAILURE,FIRE_RISK,WEAR_OUT,UNAUTHORIZED_ACCESS,FLOOD_RISK"
    monkeypatch.setenv("ENABLED_DIRECTIONS", codes)

    import app.config
    import app.main
    import app.meta.directions

    importlib.reload(app.config)
    importlib.reload(app.meta.directions)
    importlib.reload(app.main)
    yield TestClient(app.main.app)

    monkeypatch.delenv("ENABLED_DIRECTIONS")
    importlib.reload(app.config)
    importlib.reload(app.meta.directions)
    importlib.reload(app.main)


def codes_of(body: dict[str, Any]) -> list[str]:
    return [item["code"] for item in body["directions"]]


def test_four_directions_by_default() -> None:
    import app.main

    body = TestClient(app.main.app).get("/api/v1/meta").json()
    assert codes_of(body) == [
        "SENSOR_FAILURE",
        "FIRE_RISK",
        "WEAR_OUT",
        "UNAUTHORIZED_ACCESS",
    ]
    assert "FLOOD_RISK" not in body["reasons"]


def test_the_journal_and_the_map_accept_the_fifth_code(
    five_directions: TestClient,
) -> None:
    """Фильтр по новому коду обязан работать без правки роутеров.

    Прогнозов по нему нет, и пустой ответ здесь — верный ответ. Проверяется,
    что код проходит через фильтр, а не падает и не игнорируется.
    """
    journal = five_directions.get("/api/v1/predictions", params={"direction": "FLOOD_RISK"})
    assert journal.status_code == 200
    assert journal.json()["total"] == 0

    facilities = five_directions.get("/api/v1/facilities", params={"direction": "FLOOD_RISK"})
    assert facilities.status_code == 200
    assert facilities.json()["features"] == []


def test_the_fifth_direction_appears_with_its_reason_list(
    five_directions: TestClient,
) -> None:
    body = five_directions.get("/api/v1/meta").json()
    assert "FLOOD_RISK" in codes_of(body)
    assert body["reasons"]["FLOOD_RISK"], "у пятого направления нет списка причин"
    entry = next(item for item in body["directions"] if item["code"] == "FLOOD_RISK")
    assert entry["shortLabel"] and entry["accent"]
    assert entry["minHorizonHours"] >= 24
