"""Сброс демонстрационных данных.

Свойство, ради которого он написан: после сброса журнал выглядит так, будто
конвейер только что закончил счёт. Следующий проверяющий берёт в работу любую
строку, а не ищет ту, которую не тронул предыдущий.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from app.db import engine
from app.domain.reset import reset_demo
from app.tables import action_log, prediction, work_order

pytestmark = pytest.mark.usefixtures("seeded")


def run_reset() -> Any:
    with engine().begin() as conn:
        return reset_demo(conn)


def rows_of(table: Any) -> list[Any]:
    with engine().connect() as conn:
        return list(conn.execute(select(table)))


def count_of(table: Any) -> int:
    with engine().connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def test_every_prediction_becomes_new_again() -> None:
    # Фикстура держит прогнозы во всех статусах, включая терминальные.
    assert {row.status for row in rows_of(prediction)} != {"NEW"}

    run_reset()

    assert {row.status for row in rows_of(prediction)} == {"NEW"}


def test_the_decision_of_the_dispatcher_goes_away() -> None:
    """Решение диспетчера это ярлык. Чужой ярлык следующему проверяющему не нужен."""
    run_reset()

    for row in rows_of(prediction):
        assert row.assignee is None
        assert row.verdict is None
        assert row.dispatcher_level is None
        assert row.decided_at is None
        assert row.suppress_until is None


def test_orders_of_the_pipeline_return_to_the_start() -> None:
    run_reset()

    for row in rows_of(work_order):
        assert row.status == "AUTO_CREATED"
        assert row.outcome is None


def test_orders_of_the_dispatcher_disappear() -> None:
    """Заявку диспетчера породило решение. Без решения у неё нет основания."""
    with engine().begin() as conn:
        conn.execute(
            work_order.insert().values(
                id="O-MANUAL",
                number="2026-9999",
                prediction_id="P-2",
                facility_id="F-1",
                work_type="Проверка",
                due_at=rows_of(work_order)[0].due_at,
                status="CONFIRMED",
                created_by="DISPATCHER",
                created_at=rows_of(work_order)[0].created_at,
            )
        )

    run_reset()

    assert all(row.id != "O-MANUAL" for row in rows_of(work_order))


def test_the_action_log_is_cleared_of_work() -> None:
    with engine().begin() as conn:
        conn.execute(
            action_log.insert().values(
                entity_type="prediction",
                entity_id="P-1",
                action_code="take",
                actor="ods",
                payload={},
            )
        )
        conn.execute(
            action_log.insert().values(
                entity_type="auth",
                entity_id="ods",
                action_code="login",
                actor="ods",
                payload={},
            )
        )

    run_reset()

    kinds = {row.entity_type for row in rows_of(action_log)}
    # Входы остаются: они про людей, а не про работу с прогнозами.
    assert kinds == {"auth"}


def test_the_reset_keeps_the_predictions_themselves() -> None:
    """Сброс снимает следы работы, а не пересчитывает конвейер."""
    before = count_of(prediction)
    run_reset()

    assert count_of(prediction) == before


def test_the_reset_reports_numbers() -> None:
    done = run_reset()

    assert done.predictions > 0
    assert done.orders > 0


def test_a_second_reset_changes_nothing() -> None:
    run_reset()
    again = run_reset()

    assert again.predictions == 0
    assert again.orders == 0
