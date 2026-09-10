"""GET /meta строит весь интерфейс. Ошибка здесь ломает каждый экран."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.meta import catalog

client = TestClient(app)


def get_meta() -> dict[str, object]:
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_meta_has_every_contract_key() -> None:
    body = get_meta()
    assert set(body) == {
        "directions",
        "riskLevels",
        "statuses",
        "districts",
        "journalColumns",
        "orderColumns",
        "dashboardWidgets",
        "reasons",
    }


def test_every_direction_declares_a_horizon_of_at_least_24_hours() -> None:
    # Требование ТЗ. Меньший горизонт делает прогноз бесполезным.
    for item in get_meta()["directions"]:  # type: ignore[union-attr]
        assert item["minHorizonHours"] >= 24


def test_every_status_carries_a_colour_and_marks_the_terminal_ones() -> None:
    statuses = get_meta()["statuses"]
    for item in statuses:  # type: ignore[union-attr]
        assert item["colorVar"].startswith("--state-")
    terminal = {item["code"] for item in statuses if item.get("terminal")}  # type: ignore[union-attr]
    assert terminal == {"REJECTED", "CLOSED", "DONE"}


def test_rejected_appears_once_per_scope() -> None:
    statuses = get_meta()["statuses"]
    scopes = [item["scope"] for item in statuses if item["code"] == "REJECTED"]  # type: ignore[union-attr]
    assert sorted(scopes) == ["order", "prediction"]


def test_every_direction_has_a_reason_list() -> None:
    body = get_meta()
    reasons = body["reasons"]
    assert catalog.REJECTION_REASONS_REF in reasons  # type: ignore[operator]
    for item in body["directions"]:  # type: ignore[union-attr]
        assert reasons[item["code"]]  # type: ignore[index]


def test_level_for_matches_the_mock_probability_bands() -> None:
    assert catalog.level_for(0.10) == "LOW"
    assert catalog.level_for(0.30) == "MEDIUM"
    assert catalog.level_for(0.54) == "MEDIUM"
    assert catalog.level_for(0.55) == "HIGH"
    assert catalog.level_for(0.78) == "CRITICAL"
    assert catalog.level_for(1.00) == "CRITICAL"
