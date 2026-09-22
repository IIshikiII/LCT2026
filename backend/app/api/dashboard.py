"""Роутер дашборда: счётчики и верх списка риска.

Ключи счётчиков — коды из реестров, а не фиксированный набор полей. Пятое
направление добавляет ключ, и дашборд показывает его без правки роутера.
Спецификация §5 правило 1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Column, ColumnElement, func, select
from sqlalchemy.engine import Connection

from app.api.predictions import BASE, row_to_prediction
from app.auth import scope
from app.auth.deps import CurrentActor
from app.db import get_conn
from app.meta import (
    ORDER_SCOPE,
    PREDICTION_SCOPE,
    RISK_LEVELS,
    active,
    statuses_for,
)
from app.schemas import DashboardSummary, Prediction
from app.tables import prediction, work_order

router = APIRouter(tags=["dashboard"])

DEFAULT_LIMIT = 10
MAX_LIMIT = 100


def _counts(
    conn: Connection,
    column: Column[str],
    keys: tuple[str, ...],
    limit: ColumnElement[bool] | None,
) -> dict[str, int]:
    """Считает строки по колонке и раскладывает по ключам реестра.

    Ключ реестра без строк получает ноль: иначе новое направление пропало бы с
    дашборда до первого прогноза. Код из базы вне реестра тоже попадает в
    ответ, потому что молчаливая потеря счётчика хуже лишнего ключа.

    Счётчик считает то же, что видит журнал: граница роли приходит условием и
    ставится до группировки. Иначе техник видел бы на дашборде числа по всему
    предприятию и не нашёл бы эти строки в журнале.
    """
    statement = select(column, func.count()).group_by(column)
    if limit is not None:
        statement = statement.where(limit)
    rows = conn.execute(statement).all()
    found: dict[str, int] = {str(code): int(count) for code, count in rows}
    counts = {key: found.pop(key, 0) for key in keys}
    counts.update(found)
    return counts


@router.get("/dashboard/summary", response_model=DashboardSummary, response_model_by_alias=True)
def summary(
    conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor
) -> DashboardSummary:
    """Четыре словаря счётчиков и общее число прогнозов."""
    visible = scope.by_facility_id(prediction.c.facility_id, actor)
    visible_orders = scope.by_facility_id(work_order.c.facility_id, actor)

    total = select(func.count()).select_from(prediction)
    if visible is not None:
        total = total.where(visible)

    return DashboardSummary(
        by_level=_counts(
            conn, prediction.c.level, tuple(item.code for item in RISK_LEVELS), visible
        ),
        by_direction=_counts(
            conn, prediction.c.direction, tuple(item.code for item in active()), visible
        ),
        by_status=_counts(
            conn,
            prediction.c.status,
            tuple(item.code for item in statuses_for(PREDICTION_SCOPE)),
            visible,
        ),
        by_order_status=_counts(
            conn,
            work_order.c.status,
            tuple(item.code for item in statuses_for(ORDER_SCOPE)),
            visible_orders,
        ),
        total=conn.execute(total).scalar_one(),
    )


@router.get("/dashboard/top-risks", response_model=list[Prediction], response_model_by_alias=True)
def top_risks(
    conn: Annotated[Connection, Depends(get_conn)],
    actor: CurrentActor,
    limit: Annotated[int, Query()] = DEFAULT_LIMIT,
) -> list[Prediction]:
    """Самые опасные открытые прогнозы.

    Законченный прогноз в список не идёт: диспетчеру нужен список работы, а не
    история. Признак конца работы даёт `terminal` в реестре статусов, поэтому
    новый конечный статус не требует правки этого файла.
    """
    finished = [item.code for item in statuses_for(PREDICTION_SCOPE) if item.terminal]
    rows = conn.execute(
        scope.apply_joined(BASE.where(prediction.c.status.notin_(finished)), actor)
        .order_by(prediction.c.probability.desc())
        .limit(min(max(limit, 1), MAX_LIMIT))
    ).all()
    return [row_to_prediction(row) for row in rows]
