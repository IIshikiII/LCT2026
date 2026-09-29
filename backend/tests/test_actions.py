"""Действия диспетчера: переходы, проверка формы, аудит, эффекты между сущностями.

Модель статусов описана в ADR 0006 и нарисована в `docs/ARM-ODS-lifecycle.md`.
Главное правило, которое проверяет этот файл: **отклонение без последствия
невозможно**. Диспетчер называет уровень, и по итоговому уровню система сама
решает, нужна заявка или нет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import engine
from app.main import API_PREFIX, app
from app.tables import action_log
from app.tables import prediction as prediction_table
from app.tables import work_order as order_table
from tests import roles

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
            conn.execute(select(order_table).where(order_table.c.prediction_id == prediction_id))
        )


def close_by_crew(order_id: str, **body: Any) -> Any:
    """Закрывает заявку от имени группы реагирования.

    Закрыть заявку отметкой о факте может только она: отметка идёт в
    дообучение как ярлык «факт наступил», и ставит его тот, кто был на
    объекте. Диспетчер получает на это действие 403. ADR 0007.
    """
    roles.sign_in("crew")
    try:
        return act("orders", order_id, "close", **body)
    finally:
        roles.sign_in(roles.DEFAULT_USER)


def closed(order_id: str, **body: Any) -> Any:
    response = close_by_crew(order_id, **body)
    assert response.status_code == 200, response.text
    return response.json()


def decide(prediction_id: str, level: str, **extra: Any) -> Any:
    """Берёт прогноз в работу, если он ещё ничей, и принимает решение.

    Решать можно только то, что взял на себя, поэтому два шага идут вместе.
    """
    if client.get(f"{API_PREFIX}/predictions/{prediction_id}").json()["status"] == "NEW":
        ok("predictions", prediction_id, "take")
    body: dict[str, Any] = {"dispatcherLevel": level, "comment": "разобрал и решил"}
    body.update(extra)
    return ok("predictions", prediction_id, "decide", **body)


# --- взятие в работу ---------------------------------------------------------


def test_take_records_the_dispatcher() -> None:
    """Ответ заказчика 3.6: имя исполнителя в журнале обязательно."""
    body = ok("predictions", "P-1", "take")

    assert body["status"] == "IN_REVIEW"
    assert prediction_row("P-1").assignee == "ods"


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


def free_facility_of_p2() -> None:
    """Закрывает заявку O-1 объекта F-1: на объекте P-2 открытых заявок нет."""
    with engine().begin() as conn:
        conn.execute(
            order_table.update().where(order_table.c.id == "O-1").values(status="REJECTED")
        )


def test_raising_a_low_level_creates_an_order() -> None:
    """Диспетчер поднял средний до критического значит заявка обязательна.

    Это и есть отказ с последствием: до ADR 0006 отклонение среднего прогноза
    не влекло ничего, кроме подавления объекта на неделю.
    """
    free_facility_of_p2()
    body = decide("P-2", "CRITICAL")

    assert body["status"] == "ORDER_OPEN"
    row = prediction_row("P-2")
    assert row.verdict == "CORRECTED"
    assert row.dispatcher_level == "CRITICAL"

    orders = orders_of("P-2")
    assert len(orders) == 1
    # Заявка рождается подтверждённой: диспетчер только что решил, что выезд
    # нужен, и спрашивать его об этом второй раз незачем.
    assert orders[0].status == "CONFIRMED"
    assert orders[0].created_by == "DISPATCHER"


def test_agreeing_with_a_high_level_confirms_the_auto_order() -> None:
    body = decide("P-1", "CRITICAL")

    assert body["status"] == "ORDER_OPEN"
    assert prediction_row("P-1").verdict == "AGREED"
    assert [order.status for order in orders_of("P-1")] == ["CONFIRMED"]


def test_lowering_a_high_level_rejects_the_auto_order() -> None:
    """Диспетчер снизил критический до низкого значит бригада не едет."""
    body = decide("P-1", "LOW")

    assert body["status"] == "DECIDED"
    row = prediction_row("P-1")
    assert row.verdict == "CORRECTED"
    assert row.dispatcher_level == "LOW"
    assert [order.status for order in orders_of("P-1")] == ["REJECTED"]


def test_deciding_a_prediction_nobody_took_answers_409() -> None:
    """Пока прогноз ничей, решать по нему нельзя: имя исполнителя обязательно."""
    response = act("predictions", "P-1", "decide", dispatcherLevel="LOW", comment="мимо очереди")
    assert response.status_code == 409


def test_the_level_must_exist_in_the_reference_book() -> None:
    ok("predictions", "P-1", "take")
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
    ok("predictions", "P-1", "take")
    response = act("predictions", "P-1", "decide", comment="без уровня")
    assert response.status_code == 422


def test_min_length_is_checked_on_the_server() -> None:
    ok("predictions", "P-1", "take")
    response = act("predictions", "P-1", "decide", dispatcherLevel="LOW", comment="да")
    assert response.status_code == 422


def test_an_unknown_code_answers_404_and_changes_nothing() -> None:
    assert act("predictions", "P-1", "выдумка").status_code == 404
    assert client.get(f"{API_PREFIX}/predictions/P-1").json()["status"] == "NEW"


def test_an_unknown_prediction_answers_404() -> None:
    assert act("predictions", "P-404", "take").status_code == 404


# --- заявка ------------------------------------------------------------------


def test_the_order_lifecycle_runs_to_the_end() -> None:
    """Заявку подтверждает решение по прогнозу, а не отдельное действие.

    Раньше диспетчер называл исполнителя при подтверждении и бригаду при начале
    работ — одно и то же поле дважды. Теперь бригада и срок называются один раз.
    """
    decide("P-1", "CRITICAL")
    assert ok("orders", "O-1", "assign", crew="Бригада 3", dueAt=DUE)["status"] == "IN_PROGRESS"
    assert closed("O-1", **CLOSE_BODY)["status"] == "CLOSED_CONFIRMED"


def test_a_dispatcher_order_is_born_confirmed() -> None:
    """Заявка диспетчера рождается подтверждённой: он только что это решил."""
    free_facility_of_p2()
    decide("P-2", "HIGH")
    order = orders_of("P-2")[0]

    assert order.status == "CONFIRMED"
    assert order.created_by == "DISPATCHER"
    assert ok("orders", order.id, "assign", crew="Бригада 1", dueAt=DUE)["status"] == "IN_PROGRESS"


def test_deciding_a_high_level_confirms_the_waiting_order() -> None:
    """Автозаявка ждала решения и дождалась, второго подтверждения нет."""
    assert orders_of("P-1")[0].status == "AUTO_CREATED"
    decide("P-1", "CRITICAL")

    order = orders_of("P-1")[0]
    assert order.status == "CONFIRMED"
    assert order.created_by == "PIPELINE"


def test_closing_with_a_confirmed_fact_closes_the_prediction() -> None:
    decide("P-1", "CRITICAL")
    ok("orders", "O-1", "assign", crew="Бригада 3", dueAt=DUE)
    body = closed("O-1", **CLOSE_BODY)

    assert body["outcome"]["factConfirmed"] is True
    assert body["outcome"]["actualCause"] == "INTRUSION"
    assert prediction_row("P-1").status == "CLOSED_CONFIRMED"


def test_closing_without_the_fact_closes_the_prediction_the_other_way() -> None:
    """Бригада выехала и факта не нашла. Это тоже итог, а не ошибка."""
    decide("P-1", "CRITICAL")
    ok("orders", "O-1", "assign", crew="Бригада 3", dueAt=DUE)
    body = closed("O-1", **{**CLOSE_BODY, "factConfirmed": False})

    assert body["status"] == "CLOSED_NOT_CONFIRMED"
    assert prediction_row("P-1").status == "CLOSED_NOT_CONFIRMED"


def test_close_refuses_a_string_instead_of_a_boolean() -> None:
    decide("P-1", "CRITICAL")
    ok("orders", "O-1", "assign", crew="Бригада 3", dueAt=DUE)
    response = close_by_crew("O-1", **{**CLOSE_BODY, "factConfirmed": "да"})
    assert response.status_code == 422


def test_close_needs_the_confirmation_flag() -> None:
    decide("P-1", "CRITICAL")
    ok("orders", "O-1", "assign", crew="Бригада 3", dueAt=DUE)
    body = dict(CLOSE_BODY)
    del body["factConfirmed"]
    assert close_by_crew("O-1", **body).status_code == 422


def test_assign_on_a_finished_order_answers_409() -> None:
    assert act("orders", "O-4", "assign", crew="Бригада 3", dueAt=DUE).status_code == 409


def test_an_unknown_order_code_answers_404() -> None:
    assert act("orders", "O-2", "выдумка").status_code == 404
    assert client.get(f"{API_PREFIX}/orders/O-2").json()["status"] == "CONFIRMED"


def test_assign_needs_the_deadline() -> None:
    assert act("orders", "O-2", "assign", crew="Бригада 3").status_code == 422


def test_assign_refuses_a_deadline_that_is_not_a_moment() -> None:
    response = act("orders", "O-2", "assign", crew="Бригада 3", dueAt="послезавтра")
    assert response.status_code == 422


def test_rejecting_an_order_mutes_the_facility() -> None:
    ok("orders", "O-1", "reject", reason="DUPLICATE", comment="дубль вчерашней")
    assert prediction_row("P-1").suppress_until is not None


# --- аудит -------------------------------------------------------------------


def test_every_action_lands_in_the_audit_log() -> None:
    before = len(logged())
    ok("predictions", "P-1", "take")
    decide("P-1", "CRITICAL")
    ok("orders", "O-1", "assign", crew="Бригада 3", dueAt=DUE)

    # take, decide, assign
    assert len(logged()) == before + 3


def test_the_audit_keeps_the_body() -> None:
    decide("P-2", "HIGH", comment="поднял уровень, люк вскрыт")

    row = logged()[-1]
    assert row.action_code == "decide"
    assert row.payload["dispatcherLevel"] == "HIGH"
    assert row.payload["comment"] == "поднял уровень, люк вскрыт"


# --- форма решения: причина только при изменении уровня ----------------------


def test_the_decision_form_opens_with_the_model_level_and_a_locked_reason() -> None:
    """Подтверждению причина не нужна: поле открывается, когда уровень изменён."""
    body = ok("predictions", "P-1", "take")
    decide_action = next(a for a in body["actions"] if a["code"] == "decide")
    fields = {f["name"]: f for f in decide_action["fields"]}

    assert fields["dispatcherLevel"]["default"] == "CRITICAL"
    assert fields["reason"]["enabledWhen"] == {
        "field": "dispatcherLevel",
        "notEquals": "CRITICAL",
    }


def test_a_reason_sent_with_an_agreement_is_not_recorded() -> None:
    decide("P-1", "CRITICAL", reason="MODEL_ERROR")

    with engine().connect() as conn:
        logged = conn.execute(
            select(action_log.c.payload).where(
                action_log.c.entity_id == "P-1", action_log.c.action_code == "decide"
            )
        ).scalar_one()
    assert "reason" not in logged


def test_a_reason_sent_with_a_correction_is_recorded() -> None:
    decide("P-1", "LOW", reason="MODEL_ERROR")

    with engine().connect() as conn:
        logged = conn.execute(
            select(action_log.c.payload).where(
                action_log.c.entity_id == "P-1", action_log.c.action_code == "decide"
            )
        ).scalar_one()
    assert logged["reason"] == "MODEL_ERROR"


# --- одна открытая заявка на объект и направление, ADR 0022 ------------------


def test_a_decision_moves_the_untouched_order_of_the_facility() -> None:
    """P-2 лежит на объекте P-1, у которого нетронутая заявка O-1. Решение по
    P-2 «выезд нужен» забирает O-1, а не создаёт вторую."""
    decide("P-2", "HIGH")

    moved = orders_of("P-2")
    assert [order.id for order in moved] == ["O-1"]
    assert moved[0].status == "CONFIRMED"
    assert orders_of("P-1") == []


def test_a_decision_does_not_open_a_second_order_while_work_goes_on() -> None:
    """Заявка объекта уже в работе: вторую не создаём, прогноз закрыт решением."""
    with engine().begin() as conn:
        conn.execute(
            order_table.update().where(order_table.c.id == "O-1").values(status="CONFIRMED")
        )

    body = decide("P-2", "CRITICAL")

    assert body["status"] == "DECIDED"
    assert orders_of("P-2") == []
    with engine().connect() as conn:
        logged = conn.execute(
            select(action_log.c.payload).where(
                action_log.c.entity_id == "P-2", action_log.c.action_code == "decide"
            )
        ).scalar_one()
    assert logged["facilityOrderId"] == "O-1"


def test_the_database_refuses_a_second_open_order() -> None:
    """Индекс держит правило для любого пути записи."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine().begin() as conn:
        conn.execute(
            order_table.insert().values(
                id="O-dup",
                number="2026-9999",
                prediction_id="P-2",
                facility_id="F-1",
                direction="SENSOR_FAILURE",
                work_type="Диагностика датчика",
                due_at=datetime.now(UTC),
                status="AUTO_CREATED",
                created_at=datetime.now(UTC),
            )
        )
