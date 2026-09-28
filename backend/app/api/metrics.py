"""Роутер метрик: качество моделей и здоровье конвейера.

Роутер ничего не считает. Precision и Recall считает обучение и кладёт в
`model_metric`, длительности пишет прогон конвейера. Причина в спецификации §9:
метрика обязана называть способ расчёта и момент оценки, а расчёт на лету
такого следа не оставляет.

Чем метрика не является, разобрано в `docs/06-labels-and-metrics.md`. Отметка
диспетчера `predictionConfirmed` в этот расчёт не входит.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.api import common
from app.auth.deps import CurrentActor
from app.db import get_conn
from app.meta import active
from app.schemas import ModelMetric, PipelineHealth
from app.tables import model_metric, pipeline_run, prediction

router = APIRouter(tags=["metrics"])

# Требования ТЗ. Фронт их не хардкодит и берёт из ответа.
TARGET_COMPUTE_MS = 300_000
# Задержка обработки потока, ТЗ §9. ADR 0017.
TARGET_STREAM_LAG_MS = 300_000
TARGET_HORIZON_HOURS = 24

# Цели по умолчанию, те же, что у `model_metric`. Направление без замера
# держит их для строки «точность не измерена».
DEFAULT_TARGET_PRECISION = 0.7
DEFAULT_TARGET_RECALL = 0.5

# Значение статуса, которым конвейер помечает успешный прогон. Задача 10.
RUN_DONE = "DONE"

# Сколько смотреть назад в поисках прогона с событиями потока. Минуты без
# тревог обычны: на стенде события несут около 60 % прогонов.
STREAM_LOOKBACK = timedelta(hours=1)

# Свежесть, когда прогонов не было вовсе. Год заведомо вне нормы, поэтому
# виджет покажет отказ, а не зелёную галочку.
NEVER_MINUTES = 525_600.0


@router.get("/metrics/models", response_model=list[ModelMetric], response_model_by_alias=True)
def model_metrics(
    conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor
) -> list[ModelMetric]:
    """Последняя оценка по каждому включённому направлению.

    Направление без оценки в ответ не попадает. Нули вместо оценки читались бы
    как «модель не работает», хотя верное чтение — «оценки ещё нет».
    Исключение одно: направление, которое само объявило `quality_note`. Оно
    приходит строкой без чисел и с пояснением, почему точность не измерена
    (ADR 0016). Молчание о нём читалось бы как пропуск.

    Качество модели от роли не зависит: оно измерено на всей сети, и техник
    видит то же число, что диспетчер ОДС. Резать его границей видимости
    значило бы показывать четыре разных Precision у одной модели.
    """
    del actor
    newest = (
        select(model_metric)
        .distinct(model_metric.c.direction)
        .order_by(model_metric.c.direction, model_metric.c.evaluated_at.desc())
    )
    rows = {row.direction: row for row in conn.execute(newest).all()}

    # Порядок ответа задаёт реестр направлений, а не база: дашборд обязан
    # показывать направления в одном и том же порядке от прогона к прогону.
    result: list[ModelMetric] = []
    for item in active():
        row = rows.get(item.code)
        if row is not None:
            result.append(
                ModelMetric(
                    direction=item.code,
                    precision=row.precision_value,
                    recall=row.recall_value,
                    target_precision=row.target_precision,
                    target_recall=row.target_recall,
                    evaluated_at=common.iso(row.evaluated_at),
                    method=row.method,
                    baseline_rule=item.baseline_rule or None,
                    baseline_precision=row.baseline_precision,
                    baseline_recall=row.baseline_recall,
                )
            )
        elif item.quality_note:
            result.append(
                ModelMetric(
                    direction=item.code,
                    target_precision=DEFAULT_TARGET_PRECISION,
                    target_recall=DEFAULT_TARGET_RECALL,
                    evaluated_at="",
                    method=item.quality_method,
                    note=item.quality_note,
                )
            )
    return result


@router.get("/metrics/pipeline", response_model=PipelineHealth, response_model_by_alias=True)
def pipeline_health(
    conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor
) -> PipelineHealth:
    """Здоровье конвейера по последнему успешному прогону.

    Измеренные значения идут рядом с целевыми, потому что ТЗ требует
    доказательства двух чисел: прогноз считается меньше пяти минут и горизонт
    не меньше суток.

    Прогон конвейера один на предприятие, поэтому границей видимости он не
    режется: свежесть данных одинакова у всех ролей.
    """
    del actor
    run = conn.execute(
        select(pipeline_run)
        .where(pipeline_run.c.status == RUN_DONE)
        .order_by(pipeline_run.c.started_at.desc())
        .limit(1)
    ).first()

    if run is None:
        return _never_ran()

    # Прогон раз в минуту пропускает суточные направления, и последний прогон
    # часто пишет одну-две карточки или ни одной. Время и горизонт поэтому
    # берутся по прогнозам последних суток, а не одного прогона. ADR 0017.
    at = run.finished_at or run.started_at
    measured = conn.execute(
        select(
            func.max(prediction.c.compute_ms),
            func.min(prediction.c.horizon_hours),
        ).where(prediction.c.computed_at >= at - timedelta(hours=24))
    ).first()
    max_compute_ms, min_horizon = measured or (None, None)

    # Задержка потока берётся из последнего прогона, который получил события.
    # Прогон без событий задержки не знает, и виджет мигал бы «событий не
    # было» в каждую тихую минуту.
    streamed = conn.execute(
        select(pipeline_run.c.stream_lag_ms, pipeline_run.c.stream_events)
        .where(
            pipeline_run.c.status == RUN_DONE,
            pipeline_run.c.stream_events > 0,
            pipeline_run.c.started_at >= at - STREAM_LOOKBACK,
        )
        .order_by(pipeline_run.c.started_at.desc())
        .limit(1)
    ).first()

    return PipelineHealth(
        last_run_at=common.iso(at),
        last_run_ms=run.duration_ms or 0,
        freshness_minutes=_minutes_since(at),
        max_compute_ms=max_compute_ms or 0,
        min_horizon_hours=min_horizon or 0,
        target_compute_ms=TARGET_COMPUTE_MS,
        target_horizon_hours=TARGET_HORIZON_HOURS,
        stream_lag_ms=streamed.stream_lag_ms if streamed else None,
        stream_events=streamed.stream_events if streamed else 0,
        target_stream_lag_ms=TARGET_STREAM_LAG_MS,
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
        target_stream_lag_ms=TARGET_STREAM_LAG_MS,
    )


def _minutes_since(moment: datetime) -> float:
    delta = datetime.now(UTC) - moment.astimezone(UTC)
    return round(max(delta.total_seconds(), 0.0) / 60, 1)
