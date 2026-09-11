"""Роутер дашборда: счётчики и верх списка риска.

Ключи счётчиков — коды из реестров, а не фиксированный набор полей. Пятое
направление добавляет ключ, и дашборд показывает его без правки роутера.
Спецификация §5 правило 1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Column, func, select
from sqlalchemy.engine import Connection

from app.api.predictions import BASE, row_to_prediction
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


def _counts(conn: Connection, column: Column[str], keys: tuple[str, ...]) -> dict[str, int]:
    """Считает строки по колонке и раскладывает по ключам реестра.

    Ключ реестра без строк получает ноль: иначе новое направление пропало бы с
    дашборда до первого прогноза. Код из базы вне реестра тоже попадает в
    ответ, потому что молчаливая потеря счётчика хуже лишнего ключа.
    """
    rows = conn.execute(select(column, func.count()).group_by(column)).all()
    found: dict[str, int] = {str(code): int(count) for code, count in rows}
    counts = {key: found.pop(key, 0) for key in keys}
    counts.update(found)
    return counts


@router.get("/dashboard/summary", response_model=DashboardSummary, response_model_by_alias=True)
def summary(conn: Annotated[Connection, Depends(get_conn)]) -> DashboardSummary:
    """Четыре словаря счётчиков и общее число прогнозов."""
    return DashboardSummary(
        by_level=_counts(conn, prediction.c.level, tuple(item.code for item in RISK_LEVELS)),
        by_direction=_counts(conn, prediction.c.direction, tuple(item.code for item in active())),
        by_status=_counts(
            conn,
            prediction.c.status,
            tuple(item.code for item in statuses_for(PREDICTION_SCOPE)),
        ),
        by_order_status=_counts(
            conn,
            work_order.c.status,
            tuple(item.code for item in statuses_for(ORDER_SCOPE)),
        ),
        total=conn.execute(select(func.count()).select_from(prediction)).scalar_one(),
    )


@router.get("/dashboard/top-risks", response_model=list[Prediction], response_model_by_alias=True)
def top_risks(
    conn: Annotated[Connection, Depends(get_conn)],
    limit: Annotated[int, Query()] = DEFAULT_LIMIT,
) -> list[Prediction]:
    """Самые опасные открытые прогнозы.

    Законченный прогноз в список не идёт: диспетчеру нужен список работы, а не
    история. Признак конца работы даёт `terminal` в реестре статусов, поэтому
    новый конечный статус не требует правки этого файла.
    """
    finished = [item.code for item in statuses_for(PREDICTION_SCOPE) if item.terminal]
    rows = conn.execute(
        BASE.where(prediction.c.status.notin_(finished))
        .order_by(prediction.c.probability.desc())
        .limit(min(max(limit, 1), MAX_LIMIT))
    ).all()
    return [row_to_prediction(row) for row in rows]
