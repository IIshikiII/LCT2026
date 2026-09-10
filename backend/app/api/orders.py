"""Роутер заявок на превентивное обслуживание."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.engine import Connection

from app.api import common, mappers
from app.db import get_conn
from app.domain import order_actions
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
    select(work_order, facility, prediction.c.direction)
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


def _row_to_order(row: Any) -> WorkOrder:
    return mappers.work_order(
        row,
        mappers.facility_ref(row),
        list(order_actions(row.status, row.direction)),
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


@router.get("/orders/{order_id}", response_model=WorkOrder, response_model_by_alias=True)
def get_order(order_id: str, conn: Annotated[Connection, Depends(get_conn)]) -> WorkOrder:
    row = conn.execute(BASE.where(work_order.c.id == order_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"заявка {order_id} не найдена")
    return _row_to_order(row)
