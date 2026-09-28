"""Конвейер прогноза. Спецификация §7, §9.

Проверяется обход объектов и направлений, снимок признаков в
`prediction.features`, идемпотентность прогона и пропуск направления без
модели без падения всего прогона.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.pipeline import run as pipeline_run_module
from app.tables import facility, pipeline_run, prediction
from tests.conftest import reset_database

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class FakePredictor:
    """Предиктор-пустышка. Считает признаки честно, вероятность фиксирована."""

    def __init__(self, code: str, probability: float = 0.9) -> None:
        self.code = code
        self.probability = probability

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        return {"facility_marker": float(len(ctx.facility_id))}

    def predict(self, features: FeatureVector) -> float:
        return self.probability

    def explain(self, features: FeatureVector, at: datetime | None = None) -> list[Block]:
        return [
            Block(type="factors", title="Факторы", data={"items": []}),
            Block(type="timeseries", title="Динамика", data={"series": []}),
        ]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        return "Диагностика датчика"

    def label_rule(self, conn: object, facility_id: str, window: Window) -> bool:
        return False


class BrokenPredictor(FakePredictor):
    """Направление без модели. `predict` отказывает, как обещает T16."""

    def predict(self, features: FeatureVector) -> float:
        raise RuntimeError("модель направления не найдена")


@pytest.fixture
def db() -> Iterator[None]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as conn:
        reset_database(conn)
        conn.execute(
            facility.insert(),
            [
                {
                    "id": code,
                    "collector": "K-1",
                    "district": "CAO",
                    "address": f"Улица, {code}",
                    "lat": 55.75,
                    "lon": 37.61,
                    "facility_type": "chamber",
                    "is_active": True,
                }
                for code in ("F-1", "F-2")
            ],
        )

    yield

    with engine().begin() as conn:
        reset_database(conn)


def _predictors(monkeypatch: pytest.MonkeyPatch, table: dict[str, object]) -> None:
    monkeypatch.setattr(pipeline_run_module, "get_predictor", lambda code: table.get(code))


def test_a_run_writes_a_prediction_per_facility_and_direction(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})

    result = pipeline_run_module.run(engine(), at=NOW)

    assert result.prediction_count == 2
    with engine().connect() as conn:
        rows = list(conn.execute(select(prediction)))
    assert {row.facility_id for row in rows} == {"F-1", "F-2"}
    assert all(row.direction == "SENSOR_FAILURE" for row in rows)
    assert all(row.level == "CRITICAL" for row in rows)
    assert all(row.run_id == result.run_id for row in rows)


def test_the_feature_snapshot_lands_in_the_prediction_row(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})

    pipeline_run_module.run(engine(), at=NOW)

    with engine().connect() as conn:
        row = conn.execute(select(prediction).where(prediction.c.facility_id == "F-1")).one()
    assert row.features == {"facility_marker": 3.0}


def test_a_repeated_run_on_the_same_bucket_creates_nothing_new(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})

    first = pipeline_run_module.run(engine(), at=NOW)
    second = pipeline_run_module.run(engine(), at=NOW)

    assert first.prediction_count == 2
    assert second.prediction_count == 0
    with engine().connect() as conn:
        rows = list(conn.execute(select(prediction)))
    assert len(rows) == 2


def test_a_direction_without_a_model_is_skipped_not_fatal(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _predictors(
        monkeypatch,
        {
            "SENSOR_FAILURE": BrokenPredictor("SENSOR_FAILURE"),
            "FIRE_RISK": FakePredictor("FIRE_RISK"),
        },
    )

    result = pipeline_run_module.run(engine(), at=NOW)

    with engine().connect() as conn:
        directions = {row.direction for row in conn.execute(select(prediction))}
    assert directions == {"FIRE_RISK"}
    assert result.prediction_count == 2


def test_a_run_creates_orders_and_finishes_the_pipeline_run_row(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE", probability=0.95)})

    result = pipeline_run_module.run(engine(), at=NOW)

    assert result.order_count == 2
    with engine().connect() as conn:
        row = conn.execute(select(pipeline_run).where(pipeline_run.c.id == result.run_id)).one()
    assert row.status == "DONE"
    assert row.prediction_count == 2
    assert row.finished_at is not None
    assert row.model_versions == {"SENSOR_FAILURE": "latest"}


def test_a_real_run_after_the_seeded_fixture_does_not_collide_on_the_run_id(
    seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Фикстура `seeded` вставляет прогоны конвейера через `pipeline_run.insert()`.

    id этих строк раньше задавались руками (1 и 2), и последовательность
    `pipeline_run_id_seq` оставалась на единице. Первый настоящий прогон
    падал `UniqueViolation` на `pipeline_run_pkey`.
    """
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})

    result = pipeline_run_module.run(engine(), at=NOW)

    assert result.run_id is not None


def test_a_facility_in_work_gets_no_new_prediction_until_work_ends(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Диспетчер взял прогноз по F-1: следующие сутки F-1 новой карточки не
    получают, F-2 получает. Работа кончилась, и F-1 снова считается. ADR 0020."""
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})
    pipeline_run_module.run(engine(), at=NOW)
    with engine().begin() as conn:
        conn.execute(
            update(prediction).where(prediction.c.facility_id == "F-1").values(status="IN_REVIEW")
        )

    next_day = pipeline_run_module.run(engine(), at=NOW + timedelta(days=1))

    assert next_day.prediction_count == 1
    with engine().connect() as conn:
        per_facility = {
            fid: count
            for fid, count in conn.execute(
                text("SELECT facility_id, count(*) FROM prediction GROUP BY facility_id")
            )
        }
    assert per_facility == {"F-1": 1, "F-2": 2}

    with engine().begin() as conn:
        conn.execute(
            update(prediction).where(prediction.c.facility_id == "F-1").values(status="DECIDED")
        )
    after = pipeline_run_module.run(engine(), at=NOW + timedelta(days=2))
    assert after.prediction_count == 2
