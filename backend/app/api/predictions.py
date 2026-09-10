"""Роутер прогнозов: журнал, карточка, таймсерии."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.engine import Connection

from app.api import common, mappers
from app.db import get_conn, get_tx
from app.domain import apply, prediction_actions, transitions
from app.schemas import Page, Prediction, PredictionDetail, Series, SeriesPoint, TimeSeriesResponse
from app.tables import facility, prediction, sensor_reading, work_order

router = APIRouter(tags=["predictions"])

SORT_FIELDS = {
    "computedAt": prediction.c.computed_at,
    "probability": prediction.c.probability,
    "risk": prediction.c.probability,
    "horizon": prediction.c.horizon_hours,
    "direction": prediction.c.direction,
    "status": prediction.c.status,
    "summary": prediction.c.summary,
    "facility": facility.c.address,
}

# Заявка на прогноз одна. Подзапрос дешевле join с группировкой.
ORDER_ID = (
    select(work_order.c.id)
    .where(work_order.c.prediction_id == prediction.c.id)
    .limit(1)
    .scalar_subquery()
    .label("order_id")
)

JOINED = prediction.join(facility, facility.c.id == prediction.c.facility_id)

BASE = select(prediction, facility, ORDER_ID).select_from(JOINED)


def _filtered(
    statement: Select[Any],
    directions: list[str],
    levels: list[str],
    statuses: list[str],
    district: str | None,
    date_from: str | None,
    date_to: str | None,
) -> Select[Any]:
    if directions:
        statement = statement.where(prediction.c.direction.in_(directions))
    if levels:
        statement = statement.where(prediction.c.level.in_(levels))
    if statuses:
        statement = statement.where(prediction.c.status.in_(statuses))
    if district:
        statement = statement.where(facility.c.district == district)
    if date_from:
        statement = statement.where(prediction.c.computed_at >= common.day_start(date_from))
    if date_to:
        # Конец дня включительно, как в моках.
        statement = statement.where(prediction.c.computed_at <= common.day_end(date_to))
    return statement


def _row_to_prediction(row: Any) -> Prediction:
    return mappers.prediction(row, mappers.facility_ref(row), row.order_id)


@router.get("/predictions", response_model=Page[Prediction], response_model_by_alias=True)
def list_predictions(
    conn: Annotated[Connection, Depends(get_conn)],
    direction: Annotated[list[str], Query(default_factory=list)],
    level: Annotated[list[str], Query(default_factory=list)],
    status: Annotated[list[str], Query(default_factory=list)],
    district: str | None = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    sort: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = common.DEFAULT_PAGE_SIZE,
) -> Page[Prediction]:
    page, page_size = common.clamp_page(page, page_size)
    where = (direction, level, status, district, from_, to)

    total = conn.execute(_filtered(select(func.count()).select_from(JOINED), *where)).scalar_one()

    rows = conn.execute(
        _filtered(BASE, *where)
        .order_by(common.order_by(sort, SORT_FIELDS, prediction.c.computed_at.desc()))
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    return Page(
        items=[_row_to_prediction(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


def _load(conn: Connection, prediction_id: str) -> Any:
    row = conn.execute(BASE.where(prediction.c.id == prediction_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"прогноз {prediction_id} не найден")
    return row


@router.get(
    "/predictions/{prediction_id}",
    response_model=PredictionDetail,
    response_model_by_alias=True,
)
def get_prediction(
    prediction_id: str, conn: Annotated[Connection, Depends(get_conn)]
) -> PredictionDetail:
    row = _load(conn, prediction_id)
    return mappers.prediction_detail(
        _row_to_prediction(row),
        row.blocks,
        list(prediction_actions(row.status)),
    )


@router.get(
    "/predictions/{prediction_id}/timeseries",
    response_model=TimeSeriesResponse,
    response_model_by_alias=True,
)
def get_timeseries(
    prediction_id: str, conn: Annotated[Connection, Depends(get_conn)]
) -> TimeSeriesResponse:
    """Ряды показаний объекта. Окно выбирает сервер, параметров нет."""
    row = _load(conn, prediction_id)

    readings = conn.execute(
        select(
            sensor_reading.c.metric,
            sensor_reading.c.unit,
            sensor_reading.c.observed_at,
            sensor_reading.c.value,
        )
        .where(sensor_reading.c.facility_id == row.facility_id)
        .order_by(sensor_reading.c.metric, sensor_reading.c.observed_at)
    ).all()

    series: dict[str, Series] = {}
    for reading in readings:
        item = series.get(reading.metric)
        if item is None:
            item = Series(name=reading.metric, unit=reading.unit, points=[])
            series[reading.metric] = item
        item.points.append(SeriesPoint(t=common.iso(reading.observed_at), v=reading.value))

    return TimeSeriesResponse(
        series=list(series.values()),
        marker_at=common.iso(row.computed_at),
    )


@router.post(
    "/predictions/{prediction_id}/actions/{code}",
    response_model=PredictionDetail,
    response_model_by_alias=True,
)
def act_on_prediction(
    prediction_id: str,
    code: str,
    conn: Annotated[Connection, Depends(get_tx)],
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> PredictionDetail:
    """Единственный эндпоинт действий над прогнозом.

    Тело — плоский объект значений формы. Ответ — обновлённый прогноз целиком,
    чтобы фронт положил его в кэш без второго запроса.
    """
    row = _load(conn, prediction_id)
    status = row.status

    apply.check_body(list(prediction_actions(status)), code, body)
    new_status = apply.next_status(transitions.PREDICTION, code, status)

    apply.set_status(conn, prediction, prediction_id, status, new_status)
    _cascade_to_order(conn, row.order_id, code)
    apply.log(conn, transitions.PREDICTION, prediction_id, code, body)

    updated = _load(conn, prediction_id)
    return mappers.prediction_detail(
        _row_to_prediction(updated),
        updated.blocks,
        list(prediction_actions(updated.status)),
    )


def _cascade_to_order(conn: Connection, order_id: str | None, code: str) -> None:
    """Действие над прогнозом двигает его заявку. Поведение взято из моков."""
    if order_id is None:
        return
    order = conn.execute(select(work_order).where(work_order.c.id == order_id)).first()
    if order is None:
        return

    if code == "confirm_order" and order.status == "AUTO_CREATED":
        apply.set_status(conn, work_order, order_id, order.status, "CONFIRMED")
    elif code == "reject" and order.status not in ("DONE", "REJECTED"):
        apply.set_status(conn, work_order, order_id, order.status, "REJECTED")
