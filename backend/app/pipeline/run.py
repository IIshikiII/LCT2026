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

Прогон направления идёт в два прохода. Первый считает признаки, вероятность и
объяснение на каждом объекте. Второй ставит уровни и пишет строки. Между ними
плагин может назначить границы уровней по всему прогону сразу: у подтопления
HIGH держит скользящий бюджет тревог (ADR 0013).
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
from app.ml.protocol import (
    Bands,
    Block,
    FeatureContext,
    FeatureVector,
    Predictor,
    applies,
    level_bands,
    own_level,
    own_summary,
    prepare,
)
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


def _summary(direction: Direction, probability: float) -> str:
    return f"{direction.label}: вероятность {probability:.0%}"


@dataclass(frozen=True)
class Computed:
    """Прогноз одного объекта после первого прохода, до записи."""

    facility_id: str
    features: FeatureVector
    probability: float
    blocks: list[Block]
    compute_ms: int
    level: str | None = None
    summary: str | None = None


def _compute(conn: Connection, predictor: Predictor, facility_id: str, at: datetime) -> Computed:
    ctx = FeatureContext(conn=conn, facility_id=facility_id, at=at)
    started = time.monotonic()
    features = predictor.build_features(ctx)
    probability = predictor.predict(features)
    blocks = predictor.explain(features, at=at)
    return Computed(
        facility_id=facility_id,
        features=features,
        probability=probability,
        blocks=blocks,
        compute_ms=int((time.monotonic() - started) * 1000),
        level=own_level(predictor, features, probability),
        summary=own_summary(predictor, features, probability),
    )


def _stored(features: FeatureVector) -> dict[str, float | None]:
    """Признаки для JSONB. Пропуск (NaN) хранится как null: JSONB не знает NaN."""
    return {name: None if math.isnan(value) else value for name, value in features.items()}


def _write_prediction(
    conn: Connection,
    direction: Direction,
    item: Computed,
    at: datetime,
    run_id: int,
    bands: Bands | None,
) -> bool:
    """Пишет один прогноз. Отдаёт False, если прогноз уже был.

    Идемпотентность даёт уникальный индекс на паре объекта, направления и
    окна расчёта: `ON CONFLICT DO NOTHING` не плодит вторую строку при
    повторном прогоне того же окна.
    """
    statement = (
        pg_insert(prediction)
        .values(
            id=f"{direction.code}-{item.facility_id}-{int(at.timestamp())}",
            direction=direction.code,
            facility_id=item.facility_id,
            probability=item.probability,
            level=item.level or level_for(item.probability, direction, bands),
            horizon_hours=direction.min_horizon_hours,
            computed_at=at,
            computed_at_bucket=at,
            compute_ms=item.compute_ms,
            status=STATUS_NEW,
            summary=item.summary or _summary(direction, item.probability),
            blocks=[block.as_dict() for block in item.blocks],
            features=_stored(item.features),
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
                prepare(predictor, conn, at)
                computed: list[Computed] = []
                for facility_id in facility_ids:
                    if not applies(predictor, conn, facility_id):
                        continue
                    try:
                        computed.append(_compute(conn, predictor, facility_id, at))
                    except RuntimeError as error:
                        log.warning(
                            "направление пропущено: модели нет",
                            extra={"direction": direction.code, "error": str(error)},
                        )
                        del model_versions[direction.code]
                        computed = []
                        break
                if not computed:
                    continue

                fresh = {item.facility_id: (item.probability, item.features) for item in computed}
                bands = level_bands(predictor, conn, at, fresh)
                for item in computed:
                    if _write_prediction(conn, direction, item, at, run_id, bands):
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
