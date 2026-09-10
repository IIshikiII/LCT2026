"""Действия диспетчера: переходы, проверка формы, аудит, эффекты между сущностями."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import engine
from app.main import API_PREFIX, app
from app.tables import action_log

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)

CLOSE_BODY = {
    "actualCause": "INTRUSION",
    "predictionConfirmed": True,
    "comment": "проникновение подтвердилось",
}


def act(entity: str, entity_id: str, code: str, **body: Any) -> Any:
    return client.post(f"{API_PREFIX}/{entity}/{entity_id}/actions/{code}", json=body)


def ok(entity: str, entity_id: str, code: str, **body: Any) -> Any:
    response = act(entity, entity_id, code, **body)
    assert response.status_code == 200, response.text
    return response.json()


def logged() -> list[Any]:
    with engine().connect() as conn:
        return conn.execute(select(action_log)).all()


# --- прогноз ---


def test_confirm_order_moves_the_prediction_and_its_order() -> None:
    body = ok("predictions", "P-1", "confirm_order", comment="берём в работу")
    assert body["status"] == "ORDER_CONFIRMED"
    # Заявка O-1 висела в AUTO_CREATED и уходит в CONFIRMED сама.
    order = client.get(f"{API_PREFIX}/orders/O-1").json()
    assert order["status"] == "CONFIRMED"
    assert [action["code"] for action in order["actions"]] == ["start"]


def test_reject_on_a_prediction_rejects_its_open_order() -> None:
    ok("predictions", "P-1", "reject", reason="MODEL_ERROR", comment="ошибка модели")
    order = client.get(f"{API_PREFIX}/orders/O-1").json()
    assert order["status"] == "REJECTED"


def test_the_answer_carries_the_new_actions() -> None:
    body = ok("predictions", "P-1", "confirm_order", comment="ок")
    assert [action["code"] for action in body["actions"]] == ["reject"]


def test_inspect_keeps_the_prediction_in_review() -> None:
    body = ok("predictions", "P-1", "inspect", plannedAt="2026-09-11T09:00:00Z", crew="Бригада 3")
    assert body["status"] == "IN_REVIEW"


def test_an_unknown_code_answers_404_and_changes_nothing() -> None:
    # Молчаливая смена статуса по неизвестному коду скрыла бы опечатку.
    response = act("predictions", "P-1", "нет-такого-кода")
    assert response.status_code == 404
    assert "не существует" in response.json()["detail"]
    assert client.get(f"{API_PREFIX}/predictions/P-1").json()["status"] == "NEW"


def test_an_action_on_a_closed_prediction_answers_409() -> None:
    response = act("predictions", "P-4", "confirm_order", comment="поздно")
    assert response.status_code == 409
    assert "недоступно в статусе REJECTED" in response.json()["detail"]


def test_an_unknown_prediction_answers_404() -> None:
    assert act("predictions", "нет", "reject").status_code == 404


# --- проверка формы ---


def test_a_required_field_is_checked_on_the_server() -> None:
    response = act("predictions", "P-1", "reject", comment="без причины")
    assert response.status_code == 422
    assert "reason" in response.json()["detail"]


def test_min_length_is_checked_on_the_server() -> None:
    response = act("predictions", "P-1", "reject", reason="MODEL_ERROR", comment="ок")
    assert response.status_code == 422
    assert "короче 5" in response.json()["detail"]


# --- заявка ---


def test_the_order_lifecycle_runs_to_the_end() -> None:
    assert ok("orders", "O-1", "confirm", assignee="Бригада 7")["status"] == "CONFIRMED"
    assert ok("orders", "O-1", "start", crew="Бригада 7")["status"] == "IN_PROGRESS"
    closed = ok("orders", "O-1", "close", **CLOSE_BODY)
    assert closed["status"] == "DONE"


def test_close_writes_the_outcome_and_closes_the_prediction() -> None:
    body = ok("orders", "O-3", "close", **CLOSE_BODY)
    assert body["outcome"]["predictionConfirmed"] is True
    assert body["outcome"]["actualCause"] == "INTRUSION"
    assert body["outcome"]["closedAt"].endswith("Z")
    # Терминальный статус заявки — DONE, связанного прогноза — CLOSED.
    assert client.get(f"{API_PREFIX}/predictions/P-4").json()["status"] == "CLOSED"


def test_close_refuses_a_string_instead_of_a_boolean() -> None:
    # От этого поля зависят Precision и Recall, поэтому приведение типа запрещено.
    payload = {**CLOSE_BODY, "predictionConfirmed": "true"}
    response = act("orders", "O-3", "close", **payload)
    assert response.status_code == 422
    assert "predictionConfirmed" in response.json()["detail"]


def test_close_needs_the_confirmation_flag() -> None:
    payload = {key: value for key, value in CLOSE_BODY.items() if key != "predictionConfirmed"}
    assert act("orders", "O-3", "close", **payload).status_code == 422


def test_start_on_a_finished_order_answers_409() -> None:
    assert act("orders", "O-4", "start", crew="Бригада 1").status_code == 409


def test_an_unknown_order_code_answers_404() -> None:
    response = act("orders", "O-2", "нет-такого-кода")
    assert response.status_code == 404
    assert client.get(f"{API_PREFIX}/orders/O-2").json()["status"] == "CONFIRMED"


# --- аудит ---


def test_every_action_lands_in_the_audit_log() -> None:
    ok("predictions", "P-1", "confirm_order", comment="в работу")
    ok("orders", "O-2", "start", crew="Бригада 7")

    rows = logged()
    assert len(rows) == 2
    codes = {(row.entity_type, row.entity_id, row.action_code) for row in rows}
    assert ("prediction", "P-1", "confirm_order") in codes
    assert ("order", "O-2", "start") in codes


def test_the_audit_keeps_the_body() -> None:
    ok("predictions", "P-1", "reject", reason="MODEL_ERROR", comment="ошибка модели")
    row = logged()[0]
    assert row.payload == {"reason": "MODEL_ERROR", "comment": "ошибка модели"}


def test_a_refused_action_writes_nothing() -> None:
    assert act("orders", "O-4", "start", crew="Бригада 1").status_code == 409
    assert logged() == []
