"""Роутер заявок на превентивное обслуживание."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import Select, func, select, update
from sqlalchemy.engine import Connection

from app.api import common, mappers
from app.api.common import iso, parse_moment
from app.auth import scope
from app.auth.actor import Actor
from app.auth.deps import CurrentActor, check_permission
from app.db import get_conn, get_tx
from app.domain import OrderContext, apply, order_actions, suppress_until, transitions
from app.schemas import Page, WorkOrder
from app.tables import facility, prediction, work_order

router = APIRouter(tags=["orders"])

SORT_FIELDS = {
    "number": work_order.c.number,
    "dueAt": work_order.c.due_at,
    "orderStatus": work_order.c.status,
    "workType": work_order.c.work_type,
    "facility": facility.c.address,
}

# Направление приходит из связанного прогноза: форма закрытия берёт по нему
# список фактических причин.
BASE = (
    select(
        work_order,
        facility,
        prediction.c.direction,
        prediction.c.computed_at,
        prediction.c.horizon_hours,
    )
    .join(facility, facility.c.id == work_order.c.facility_id)
    .join(prediction, prediction.c.id == work_order.c.prediction_id)
)

COUNT_FROM = work_order.join(facility, facility.c.id == work_order.c.facility_id)


def _filtered(
    statement: Select[Any], actor: Actor, statuses: list[str], due_before: str | None
) -> Select[Any]:
    statement = scope.apply_joined(statement, actor)
    if statuses:
        statement = statement.where(work_order.c.status.in_(statuses))
    if due_before:
        statement = statement.where(work_order.c.due_at <= common.day_end(due_before))
    return statement


def _context(row: Any) -> OrderContext:
    return OrderContext(
        direction=row.direction,
        computed_at=row.computed_at,
        horizon_hours=row.horizon_hours,
    )


def _row_to_order(row: Any, actor: Actor) -> WorkOrder:
    return mappers.work_order(
        row,
        mappers.facility_ref(row),
        list(order_actions(row.status, _context(row), actor)),
    )


@router.get("/orders", response_model=Page[WorkOrder], response_model_by_alias=True)
def list_orders(
    conn: Annotated[Connection, Depends(get_conn)],
    actor: CurrentActor,
    status: Annotated[list[str], Query(default_factory=list)],
    due_before: Annotated[str | None, Query(alias="dueBefore")] = None,
    sort: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = common.DEFAULT_PAGE_SIZE,
) -> Page[WorkOrder]:
    page, page_size = common.clamp_page(page, page_size)

    total = conn.execute(
        _filtered(select(func.count()).select_from(COUNT_FROM), actor, status, due_before)
    ).scalar_one()

    rows = conn.execute(
        _filtered(BASE, actor, status, due_before)
        .order_by(common.order_by(sort, SORT_FIELDS, work_order.c.due_at.asc()))
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    return Page(
        items=[_row_to_order(row, actor) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


def _load(conn: Connection, actor: Actor, order_id: str) -> Any:
    """Читает заявку в границах видимости роли. Чужая заявка даёт 404."""
    row = conn.execute(scope.apply_joined(BASE.where(work_order.c.id == order_id), actor)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"заявка {order_id} не найдена")
    return row


@router.get("/orders/{order_id}", response_model=WorkOrder, response_model_by_alias=True)
def get_order(
    order_id: str, conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor
) -> WorkOrder:
    return _row_to_order(_load(conn, actor, order_id), actor)


@router.post(
    "/orders/{order_id}/actions/{code}",
    response_model=WorkOrder,
    response_model_by_alias=True,
)
def act_on_order(
    order_id: str,
    code: str,
    conn: Annotated[Connection, Depends(get_tx)],
    actor: CurrentActor,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> WorkOrder:
    """Единственный эндпоинт действий над заявкой.

    Права делят действия между диспетчером и группой реагирования. Закрыть
    заявку отметкой о факте может только группа реагирования: отметка идёт в
    дообучение как ярлык «факт наступил», и ставит её тот, кто был на объекте.
    """
    apply.require_known(transitions.ORDER, code)
    check_permission(conn, actor, code)
    row = _load(conn, actor, order_id)
    status = row.status

    apply.check_body(list(order_actions(status, _context(row), actor)), code, body)
    new_status = apply.next_status(transitions.ORDER, code, status)

    extra: dict[str, Any] = {}
    if code == "assign":
        # Срок называет диспетчер вместе с бригадой, одним действием. Расчётный
        # срок автосоздания был заглушкой от горизонта.
        extra["due_at"] = parse_moment(body["dueAt"], field="dueAt")
    if code == "close":
        extra["outcome"] = _outcome(body)
        # Исход бригады выбирает терминальный статус. Таблица переходов знает
        # только один из двух, потому что он зависит от ответа формы. ADR 0006.
        new_status = transitions.CLOSE_OUTCOME[bool(body.get("factConfirmed"))]

    apply.set_status(conn, work_order, order_id, status, new_status, **extra)

    if code == "close":
        # Заявка закрывает прогноз. Своего поля исхода у прогноза нет: два поля
        # с одним смыслом разошлись бы на первой же правке. ADR 0006.
        conn.execute(
            update(prediction).where(prediction.c.id == row.prediction_id).values(status=new_status)
        )

    if code == "reject":
        # Прогноз может остаться в работе, но решение «бригаду не шлём» принято.
        # Предлагать ту же заявку снова нельзя.
        conn.execute(
            update(prediction)
            .where(prediction.c.id == row.prediction_id)
            .values(suppress_until=_mute_until(body, row.direction))
        )

    apply.log(conn, transitions.ORDER, order_id, code, actor.username, body)
    return _row_to_order(_load(conn, actor, order_id), actor)


def _mute_until(body: dict[str, Any], direction: str) -> datetime:
    """Момент, до которого направление на объекте не предлагает новых заявок."""
    named = body.get("suppressUntil")
    if named:
        return parse_moment(named, field="suppressUntil")
    return suppress_until(body.get("reason"), direction, datetime.now(UTC))


def _outcome(body: dict[str, Any]) -> dict[str, Any]:
    """Итог работ на объекте. Второй ярлык для дообучения. ADR 0006.

    Поле называется `factConfirmed`, а не `predictionConfirmed`: бригада видела
    факт, а не пользу выезда. Пользу измерить нечем, и старое имя смешивало две
    разные величины, что запрещает `docs/06-labels-and-metrics.md`.
    """
    return {
        "actualCause": str(body.get("actualCause", "")),
        "factConfirmed": bool(body["factConfirmed"]),
        "comment": str(body.get("comment", "")),
        "closedAt": iso(datetime.now(UTC)),
    }
