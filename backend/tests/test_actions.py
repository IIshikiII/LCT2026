"""Действия диспетчера: переходы, проверка формы, аудит, эффекты между сущностями.

Модель статусов описана в ADR 0006 и нарисована в `ARM-ODS-lifecycle.md`.
Главное правило, которое проверяет этот файл: **отклонение без последствия
невозможно**. Диспетчер называет уровень, и по итоговому уровню система сама
решает, нужна заявка или нет.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import engine
from app.main import API_PREFIX, app
from app.tables import action_log
from app.tables import prediction as prediction_table
from app.tables import work_order as order_table

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)

DUE = "2026-09-12T09:00:00Z"

CLOSE_BODY = {
    "actualCause": "INTRUSION",
    "factConfirmed": True,
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


def prediction_row(prediction_id: str) -> Any:
    with engine().connect() as conn:
        return conn.execute(
            select(prediction_table).where(prediction_table.c.id == prediction_id)
        ).one()


def orders_of(prediction_id: str) -> list[Any]:
    with engine().connect() as conn:
        return list(
            conn.execute(
                select(order_table).where(order_table.c.prediction_id == prediction_id)
            )
        )


def decide(prediction_id: str, level: str, **extra: Any) -> Any:
    body: dict[str, Any] = {"dispatcherLevel": level, "comment": "разобрал и решил"}
    body.update(extra)
    return ok("predictions", prediction_id, "decide", **body)


# --- взятие в работу ---------------------------------------------------------


def test_take_records_the_dispatcher() -> None:
    """Ответ заказчика 3.6: имя исполнителя в журнале обязательно."""
    body = ok("predictions", "P-1", "take")

    assert body["status"] == "IN_REVIEW"
    assert prediction_row("P-1").assignee == "dispatcher"


def test_release_returns_the_prediction_to_the_queue() -> None:
    ok("predictions", "P-1", "take")
    body = ok("predictions", "P-1", "release")

    assert body["status"] == "NEW"
    assert prediction_row("P-1").assignee is None


def test_take_is_not_offered_twice() -> None:
    ok("predictions", "P-1", "take")
    assert act("predictions", "P-1", "take").status_code == 409


# --- решение диспетчера ------------------------------------------------------


def test_agreeing_with_a_low_level_closes_the_prediction() -> None:
    """Низкий уровень выезда не требует, и история на этом кончается."""
    body = decide("P-2", "MEDIUM")

    assert body["status"] == "DECIDED"
    row = prediction_row("P-2")
    assert row.verdict == "AGREED"
    assert row.dispatcher_level == "MEDIUM"
    assert row.decided_at is not None
    assert body["actions"] == []


def test_raising_a_low_level_creates_an_order() -> None:
    """Диспетчер поднял средний до критического значит заявка обязательна.

    Это и есть отказ с последствием: до ADR 0006 отклонение среднего прогноза
    не влекло ничего, кроме подавления объекта на неделю.
    """
    body = decide("P-2", "CRITICAL")

    assert body["status"] == "ORDER_OPEN"
    row = prediction_row("P-2")
    assert row.verdict == "CORRECTED"
    assert row.dispatcher_level == "CRITICAL"

    orders = orders_of("P-2")
    assert len(orders) == 1
    assert orders[0].status == "MANUAL_CREATED"


def test_agreeing_with_a_high_level_keeps_the_auto_order() -> None:
    body = decide("P-1", "CRITICAL")

    assert body["status"] == "ORDER_OPEN"
    assert prediction_row("P-1").verdict == "AGREED"
    assert [order.status for order in orders_of("P-1")] == ["AUTO_CREATED"]


def test_lowering_a_high_level_rejects_the_auto_order() -> None:
    """Диспетчер снизил критический до низкого значит бригада не едет."""
    body = decide("P-1", "LOW")

    assert body["status"] == "DECIDED"
    row = prediction_row("P-1")
    assert row.verdict == "CORRECTED"
    assert row.dispatcher_level == "LOW"
    assert [order.status for order in orders_of("P-1")] == ["REJECTED"]


def test_the_level_must_exist_in_the_reference_book() -> None:
    response = act(
        "predictions", "P-1", "decide", dispatcherLevel="ОЧЕНЬ_СТРАШНО", comment="ерунда"
    )
    assert response.status_code == 422
    assert "riskLevels" in response.json()["detail"]


def test_deciding_twice_answers_409() -> None:
    decide("P-2", "MEDIUM")
    response = act("predictions", "P-2", "decide", dispatcherLevel="LOW", comment="ещё раз")
    assert response.status_code == 409


def test_a_terminal_prediction_offers_no_actions() -> None:
    body = client.get(f"{API_PREFIX}/predictions/P-5").json()
    assert body["status"] == "CLOSED_CONFIRMED"
    assert body["actions"] == []


# --- проверка формы ----------------------------------------------------------


def test_a_required_field_is_checked_on_the_server() -> None:
    response = act("predictions", "P-1", "decide", comment="без уровня")
    assert response.status_code == 422


def test_min_length_is_checked_on_the_server() -> None:
    response = act("predictions", "P-1", "decide", dispatcherLevel="LOW", comment="да")
    assert response.status_code == 422


def test_an_unknown_code_answers_404_and_changes_nothing() -> None:
    assert act("predictions", "P-1", "выдумка").status_code == 404
    assert client.get(f"{API_PREFIX}/predictions/P-1").json()["status"] == "NEW"


def test_an_unknown_prediction_answers_404() -> None:
    assert act("predictions", "P-404", "take").status_code == 404


# --- заявка ------------------------------------------------------------------


def test_the_order_lifecycle_runs_to_the_end() -> None:
    assert ok("orders", "O-1", "confirm", assignee="Иванов", dueAt=DUE)["status"] == "CONFIRMED"
    assert ok("orders", "O-1", "start", crew="Бригада 3")["status"] == "IN_PROGRESS"
    assert ok("orders", "O-1", "close", **CLOSE_BODY)["status"] == "CLOSED_CONFIRMED"


def test_a_dispatcher_order_follows_the_same_path() -> None:
    """Заявка диспетчера отличается происхождением, а не жизненным циклом."""
    decide("P-2", "HIGH")
    order_id = orders_of("P-2")[0].id

    assert ok("orders", order_id, "confirm", assignee="Иванов", dueAt=DUE)["status"] == "CONFIRMED"
    assert ok("orders", order_id, "start", crew="Бригада 1")["status"] == "IN_PROGRESS"


def test_closing_with_a_confirmed_fact_closes_the_prediction() -> None:
    ok("orders", "O-1", "confirm", assignee="Иванов", dueAt=DUE)
    ok("orders", "O-1", "start", crew="Бригада 3")
    body = ok("orders", "O-1", "close", **CLOSE_BODY)

    assert body["outcome"]["factConfirmed"] is True
    assert body["outcome"]["actualCause"] == "INTRUSION"
    assert prediction_row("P-1").status == "CLOSED_CONFIRMED"


def test_closing_without_the_fact_closes_the_prediction_the_other_way() -> None:
    """Бригада выехала и факта не нашла. Это тоже итог, а не ошибка."""
    ok("orders", "O-1", "confirm", assignee="Иванов", dueAt=DUE)
    ok("orders", "O-1", "start", crew="Бригада 3")
    body = ok("orders", "O-1", "close", **{**CLOSE_BODY, "factConfirmed": False})

    assert body["status"] == "CLOSED_NOT_CONFIRMED"
    assert prediction_row("P-1").status == "CLOSED_NOT_CONFIRMED"


def test_close_refuses_a_string_instead_of_a_boolean() -> None:
    ok("orders", "O-1", "confirm", assignee="Иванов", dueAt=DUE)
    ok("orders", "O-1", "start", crew="Бригада 3")
    response = act("orders", "O-1", "close", **{**CLOSE_BODY, "factConfirmed": "да"})
    assert response.status_code == 422


def test_close_needs_the_confirmation_flag() -> None:
    ok("orders", "O-1", "confirm", assignee="Иванов", dueAt=DUE)
    ok("orders", "O-1", "start", crew="Бригада 3")
    body = dict(CLOSE_BODY)
    del body["factConfirmed"]
    assert act("orders", "O-1", "close", **body).status_code == 422


def test_start_on_a_finished_order_answers_409() -> None:
    assert act("orders", "O-4", "start", crew="Бригада 3").status_code == 409


def test_an_unknown_order_code_answers_404() -> None:
    assert act("orders", "O-2", "выдумка").status_code == 404
    assert client.get(f"{API_PREFIX}/orders/O-2").json()["status"] == "CONFIRMED"


def test_confirm_needs_the_deadline() -> None:
    assert act("orders", "O-1", "confirm", assignee="Иванов").status_code == 422


def test_confirm_refuses_a_deadline_that_is_not_a_moment() -> None:
    response = act("orders", "O-1", "confirm", assignee="Иванов", dueAt="послезавтра")
    assert response.status_code == 422


def test_rejecting_an_order_mutes_the_facility() -> None:
    ok("orders", "O-1", "reject", reason="DUPLICATE", comment="дубль вчерашней")
    assert prediction_row("P-1").suppress_until is not None


# --- аудит -------------------------------------------------------------------


def test_every_action_lands_in_the_audit_log() -> None:
    before = len(logged())
    ok("predictions", "P-1", "take")
    decide("P-1", "CRITICAL")
    ok("orders", "O-1", "confirm", assignee="Иванов", dueAt=DUE)

    assert len(logged()) == before + 3


def test_the_audit_keeps_the_body() -> None:
    decide("P-2", "HIGH", comment="поднял уровень, люк вскрыт")

    row = logged()[-1]
    assert row.action_code == "decide"
    assert row.payload["dispatcherLevel"] == "HIGH"
    assert row.payload["comment"] == "поднял уровень, люк вскрыт"
