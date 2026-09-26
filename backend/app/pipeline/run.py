"""Конвейер прогноза. Спецификация §7, §9.

Обходит активные объекты, вызывает плагин каждого включённого направления и
пишет прогноз в `prediction` со снимком признаков. После записи прогнозов
запускает создание автоматических заявок тем же прогоном. Итог записывается в
`pipeline_run`, чтобы дашборд видел здоровье последнего прохода.

Направление без обученной модели поднимает `RuntimeError` из `predict` —
решение об этом уже приняли плагин и T16. Конвейер ловит эту ошибку и
пропускает направление молча для всех объектов: модель отсутствует для
направления целиком, а не для одного объекта, поэтому повторять попытку на
каждом объекте бессмысленно.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.domain import auto_orders
from app.meta import active as active_directions
from app.meta import level_for
from app.meta.directions import Direction
from app.ml.protocol import FeatureContext, Predictor
from app.ml.registry import get as get_predictor
from app.ml.registry import missing as missing_predictors
from app.tables import facility, pipeline_run, prediction

log = logging.getLogger(__name__)

STATUS_NEW = "NEW"
DEFAULT_MODEL_VERSION = "latest"


@dataclass(frozen=True)
class RunResult:
    """Итог одного прогона. Возвращается вызывающему коду, не хранится."""

    run_id: int
    prediction_count: int
    order_count: int
    duration_ms: int


def _facility_ids(conn: Connection) -> list[str]:
    statement = select(facility.c.id).where(facility.c.is_active.is_(True)).order_by(facility.c.id)
    return list(conn.execute(statement).scalars().all())


def _storable(features: dict[str, float]) -> dict[str, float | None]:
    """Вектор для `prediction.features`. JSONB не принимает NaN, а пустой
    признак модель получает именно так. В базу он ложится как `null`."""
    return {name: None if math.isnan(value) else value for name, value in features.items()}


def _summary(direction: Direction, probability: float) -> str:
    return f"{direction.label}: вероятность {probability:.0%}"


def _write_prediction(
    conn: Connection,
    predictor: Predictor,
    direction: Direction,
    facility_id: str,
    at: datetime,
    run_id: int,
) -> bool:
    """Строит и пишет один прогноз. Отдаёт False, если прогноз уже был.

    Идемпотентность даёт уникальный индекс на паре объекта, направления и
    окна расчёта: `ON CONFLICT DO NOTHING` не плодит вторую строку при
    повторном прогоне того же окна.
    """
    ctx = FeatureContext(conn=conn, facility_id=facility_id, at=at)
    started = time.monotonic()
    features = predictor.build_features(ctx)
    probability = predictor.predict(features)
    blocks = predictor.explain(features, at=at)
    compute_ms = int((time.monotonic() - started) * 1000)

    statement = (
        pg_insert(prediction)
        .values(
            id=f"{direction.code}-{facility_id}-{int(at.timestamp())}",
            direction=direction.code,
            facility_id=facility_id,
            probability=probability,
            level=level_for(probability, direction),
            horizon_hours=direction.min_horizon_hours,
            computed_at=at,
            computed_at_bucket=at,
            compute_ms=compute_ms,
            status=STATUS_NEW,
            summary=_summary(direction, probability),
            blocks=[block.as_dict() for block in blocks],
            features=_storable(features),
            model_version=DEFAULT_MODEL_VERSION,
            run_id=run_id,
        )
        .on_conflict_do_nothing(index_elements=["facility_id", "direction", "computed_at_bucket"])
        .returning(prediction.c.id)
    )
    return conn.execute(statement).first() is not None


def run(engine: Engine, at: datetime | None = None) -> RunResult:
    """Прогоняет все активные объекты и направления, пишет итог в `pipeline_run`."""
    at = (at or datetime.now(UTC)).replace(microsecond=0)
    started = time.monotonic()

    with engine.begin() as conn:
        inserted = conn.execute(
            pipeline_run.insert().values(started_at=at, status="RUNNING")
        ).inserted_primary_key
        assert inserted is not None
        run_id = inserted[0]

    prediction_count = 0
    order_count = 0
    model_versions: dict[str, str] = {}

    try:
        with engine.begin() as conn:
            facility_ids = _facility_ids(conn)

            for direction in active_directions():
                predictor = get_predictor(direction.code)
                if predictor is None:
                    continue

                model_versions[direction.code] = DEFAULT_MODEL_VERSION
                for facility_id in facility_ids:
                    try:
                        created = _write_prediction(
                            conn, predictor, direction, facility_id, at, run_id
                        )
                    except RuntimeError as error:
                        log.warning(
                            "направление пропущено: модели нет",
                            extra={"direction": direction.code, "error": str(error)},
                        )
                        del model_versions[direction.code]
                        break
                    if created:
                        prediction_count += 1

            order_count = len(auto_orders.create_missing(conn, at))
    except Exception as error:
        duration_ms = int((time.monotonic() - started) * 1000)
        with engine.begin() as conn:
            conn.execute(
                pipeline_run.update()
                .where(pipeline_run.c.id == run_id)
                .values(
                    finished_at=datetime.now(UTC),
                    duration_ms=duration_ms,
                    status="FAILED",
                    error=str(error),
                )
            )
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    with engine.begin() as conn:
        conn.execute(
            pipeline_run.update()
            .where(pipeline_run.c.id == run_id)
            .values(
                finished_at=datetime.now(UTC),
                duration_ms=duration_ms,
                prediction_count=prediction_count,
                model_versions=model_versions,
                status="DONE",
            )
        )

    still_missing = missing_predictors()
    if still_missing:
        log.info("направления без предиктора пропущены", extra={"directions": still_missing})

    return RunResult(
        run_id=run_id,
        prediction_count=prediction_count,
        order_count=order_count,
        duration_ms=duration_ms,
    )
