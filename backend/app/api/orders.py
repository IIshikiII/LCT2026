"""Роутер заявок на превентивное обслуживание."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import Select, func, select, update
from sqlalchemy.engine import Connection

from app.api import common, mappers
from app.api.common import iso, parse_moment
from app.db import get_conn, get_tx
from app.domain import OrderContext, apply, order_actions, transitions
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


def _filtered(statement: Select[Any], statuses: list[str], due_before: str | None) -> Select[Any]:
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


def _row_to_order(row: Any) -> WorkOrder:
    return mappers.work_order(
        row,
        mappers.facility_ref(row),
        list(order_actions(row.status, _context(row))),
    )


@router.get("/orders", response_model=Page[WorkOrder], response_model_by_alias=True)
def list_orders(
    conn: Annotated[Connection, Depends(get_conn)],
    status: Annotated[list[str], Query(default_factory=list)],
    due_before: Annotated[str | None, Query(alias="dueBefore")] = None,
    sort: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = common.DEFAULT_PAGE_SIZE,
) -> Page[WorkOrder]:
    page, page_size = common.clamp_page(page, page_size)

    total = conn.execute(
        _filtered(select(func.count()).select_from(COUNT_FROM), status, due_before)
    ).scalar_one()

    rows = conn.execute(
        _filtered(BASE, status, due_before)
        .order_by(common.order_by(sort, SORT_FIELDS, work_order.c.due_at.asc()))
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    return Page(
        items=[_row_to_order(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


def _load(conn: Connection, order_id: str) -> Any:
    row = conn.execute(BASE.where(work_order.c.id == order_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"заявка {order_id} не найдена")
    return row


@router.get("/orders/{order_id}", response_model=WorkOrder, response_model_by_alias=True)
def get_order(order_id: str, conn: Annotated[Connection, Depends(get_conn)]) -> WorkOrder:
    return _row_to_order(_load(conn, order_id))


@router.post(
    "/orders/{order_id}/actions/{code}",
    response_model=WorkOrder,
    response_model_by_alias=True,
)
def act_on_order(
    order_id: str,
    code: str,
    conn: Annotated[Connection, Depends(get_tx)],
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> WorkOrder:
    """Единственный эндпоинт действий над заявкой."""
    row = _load(conn, order_id)
    status = row.status

    apply.check_body(list(order_actions(status, _context(row))), code, body)
    new_status = apply.next_status(transitions.ORDER, code, status)

    extra: dict[str, Any] = {}
    if code == "confirm":
        # Срок назначает диспетчер. Расчётный срок автосоздания был заглушкой.
        extra["due_at"] = parse_moment(body["dueAt"], field="dueAt")
    if code == "close":
        extra["outcome"] = _outcome(body)

    apply.set_status(conn, work_order, order_id, status, new_status, **extra)

    if code == "close":
        # Терминальный статус заявки — DONE, связанного прогноза — CLOSED.
        conn.execute(
            update(prediction).where(prediction.c.id == row.prediction_id).values(status="CLOSED")
        )

    apply.log(conn, transitions.ORDER, order_id, code, body)
    return _row_to_order(_load(conn, order_id))


def _outcome(body: dict[str, Any]) -> dict[str, Any]:
    """Итог работ. Отсюда берутся честные Precision и Recall."""
    return {
        "actualCause": str(body.get("actualCause", "")),
        "predictionConfirmed": body["predictionConfirmed"],
        "comment": str(body.get("comment", "")),
        "closedAt": iso(datetime.now(UTC)),
    }
