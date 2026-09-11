"""Роутер метрик: качество моделей и здоровье конвейера.

Роутер ничего не считает. Precision и Recall считает обучение и кладёт в
`model_metric`, длительности пишет прогон конвейера. Причина в спецификации §9:
метрика обязана называть способ расчёта и момент оценки, а расчёт на лету
такого следа не оставляет.

Чем метрика не является, разобрано в `docs/06-labels-and-metrics.md`. Отметка
диспетчера `predictionConfirmed` в этот расчёт не входит.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.api import common
from app.db import get_conn
from app.meta import active
from app.schemas import ModelMetric, PipelineHealth
from app.tables import model_metric, pipeline_run, prediction

router = APIRouter(tags=["metrics"])

# Требования ТЗ. Фронт их не хардкодит и берёт из ответа.
TARGET_COMPUTE_MS = 300_000
TARGET_HORIZON_HOURS = 24

# Значение статуса, которым конвейер помечает успешный прогон. Задача 10.
RUN_DONE = "DONE"

# Свежесть, когда прогонов не было вовсе. Год заведомо вне нормы, поэтому
# виджет покажет отказ, а не зелёную галочку.
NEVER_MINUTES = 525_600.0


@router.get("/metrics/models", response_model=list[ModelMetric], response_model_by_alias=True)
def model_metrics(conn: Annotated[Connection, Depends(get_conn)]) -> list[ModelMetric]:
    """Последняя оценка по каждому включённому направлению.

    Направление без оценки в ответ не попадает. Нули вместо оценки читались бы
    как «модель не работает», хотя верное чтение — «оценки ещё нет».
    """
    newest = (
        select(model_metric)
        .distinct(model_metric.c.direction)
        .order_by(model_metric.c.direction, model_metric.c.evaluated_at.desc())
    )
    rows = {row.direction: row for row in conn.execute(newest).all()}

    # Порядок ответа задаёт реестр направлений, а не база: дашборд обязан
    # показывать направления в одном и том же порядке от прогона к прогону.
    return [
        ModelMetric(
            direction=item.code,
            precision=rows[item.code].precision_value,
            recall=rows[item.code].recall_value,
            target_precision=rows[item.code].target_precision,
            target_recall=rows[item.code].target_recall,
            evaluated_at=common.iso(rows[item.code].evaluated_at),
        )
        for item in active()
        if item.code in rows
    ]


@router.get("/metrics/pipeline", response_model=PipelineHealth, response_model_by_alias=True)
def pipeline_health(conn: Annotated[Connection, Depends(get_conn)]) -> PipelineHealth:
    """Здоровье конвейера по последнему успешному прогону.

    Измеренные значения идут рядом с целевыми, потому что ТЗ требует
    доказательства двух чисел: прогноз считается меньше пяти минут и горизонт
    не меньше суток.
    """
    run = conn.execute(
        select(pipeline_run)
        .where(pipeline_run.c.status == RUN_DONE)
        .order_by(pipeline_run.c.started_at.desc())
        .limit(1)
    ).first()

    if run is None:
        return _never_ran()

    measured = conn.execute(
        select(
            func.max(prediction.c.compute_ms),
            func.min(prediction.c.horizon_hours),
        ).where(prediction.c.run_id == run.id)
    ).first()
    max_compute_ms, min_horizon = measured or (None, None)

    at = run.finished_at or run.started_at
    return PipelineHealth(
        last_run_at=common.iso(at),
        last_run_ms=run.duration_ms or 0,
        freshness_minutes=_minutes_since(at),
        max_compute_ms=max_compute_ms or 0,
        min_horizon_hours=min_horizon or 0,
        target_compute_ms=TARGET_COMPUTE_MS,
        target_horizon_hours=TARGET_HORIZON_HOURS,
    )


def _never_ran() -> PipelineHealth:
    """Ответ до первого прогона.

    Горизонт 0 и свежесть в год показывают отказ по обоим требованиям ТЗ. Это
    верно: пока прогонов нет, ни одно из двух чисел не доказано.
    """
    return PipelineHealth(
        last_run_at="",
        last_run_ms=0,
        freshness_minutes=NEVER_MINUTES,
        max_compute_ms=0,
        min_horizon_hours=0,
        target_compute_ms=TARGET_COMPUTE_MS,
        target_horizon_hours=TARGET_HORIZON_HOURS,
    )


def _minutes_since(moment: datetime) -> float:
    delta = datetime.now(UTC) - moment.astimezone(UTC)
    return round(max(delta.total_seconds(), 0.0) / 60, 1)
