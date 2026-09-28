"""Ритм направлений и карточка на происшествие. ADR 0017.

Суточное направление считается один раз за московские сутки на момент
полуночи. Потоковое считается каждым прогоном и обновляет карточку своего
происшествия, пока её не взял диспетчер. Прогон пишет задержку потока.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.ml.protocol import Block, FeatureContext, FeatureVector, Window
from app.pipeline import run as pipeline_run_module
from app.pipeline.run import day_start
from app.tables import facility, pipeline_run, prediction
from tests.conftest import reset_database

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
EPISODE = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)


class Daily:
    """Суточное направление: признаки помнят момент расчёта."""

    code = "SENSOR_FAILURE"

    def __init__(self) -> None:
        self.points: list[datetime] = []

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        self.points.append(ctx.at)
        return {"hour": float(ctx.at.hour)}

    def predict(self, features: FeatureVector) -> float:
        return 0.9

    def explain(self, features: FeatureVector, at: datetime | None = None) -> list[Block]:
        return [
            Block(type="factors", title="Факторы", data={"items": []}),
            Block(type="timeseries", title="Динамика", data={"series": []}),
        ]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        return "Диагностика датчика"

    def label_rule(self, conn: object, facility_id: str, window: Window) -> bool:
        return False


class Stream(Daily):
    """Потоковое направление с происшествием и кандидатами."""

    code = "FIRE_RISK"

    def __init__(self, probability: float = 0.2, chosen: set[str] | None = None) -> None:
        super().__init__()
        self.probability = probability
        self.chosen = chosen
        self.seen: list[str] = []

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        self.seen.append(ctx.facility_id)
        return super().build_features(ctx)

    def predict(self, features: FeatureVector) -> float:
        return self.probability

    def level(self, features: FeatureVector, probability: float) -> str:
        return "HIGH" if probability >= 0.5 else "MEDIUM"

    def incident(self, features: FeatureVector, at: datetime) -> datetime | None:
        return EPISODE

    def candidates(self, conn: object, at: datetime) -> set[str] | None:
        return self.chosen


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
                    "facility_type": "picket",
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


def _rows(direction: str) -> list[object]:
    with engine().connect() as conn:
        return list(
            conn.execute(
                select(prediction)
                .where(prediction.c.direction == direction)
                .order_by(prediction.c.facility_id)
            )
        )


def test_the_moscow_day_starts_at_21_utc() -> None:
    assert day_start(NOW) == datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
    assert day_start(datetime(2026, 9, 17, 21, 0, tzinfo=UTC)) == datetime(
        2026, 9, 17, 21, 0, tzinfo=UTC
    )


def test_a_daily_direction_runs_once_a_day_at_midnight(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = Daily()
    _predictors(monkeypatch, {"SENSOR_FAILURE": daily})

    first = pipeline_run_module.run(engine(), at=NOW)
    later = pipeline_run_module.run(engine(), at=NOW + timedelta(hours=3))

    assert first.prediction_count == 2
    assert later.prediction_count == 0
    assert daily.points == [day_start(NOW)] * 2
    rows = _rows("SENSOR_FAILURE")
    assert {r.computed_at_bucket for r in rows} == {day_start(NOW)}

    next_day = pipeline_run_module.run(engine(), at=NOW + timedelta(days=1))
    assert next_day.prediction_count == 2


def test_a_stream_direction_updates_the_card_of_its_incident(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = Stream(probability=0.2)
    _predictors(monkeypatch, {"FIRE_RISK": stream})
    first = pipeline_run_module.run(engine(), at=NOW)

    stream.probability = 0.8
    second = pipeline_run_module.run(engine(), at=NOW + timedelta(minutes=5))

    assert first.prediction_count == 2
    assert second.prediction_count == 0
    rows = _rows("FIRE_RISK")
    assert len(rows) == 2
    assert all(r.computed_at_bucket == EPISODE for r in rows)
    assert all(r.level == "HIGH" and r.probability == 0.8 for r in rows)
    assert all(r.computed_at == NOW + timedelta(minutes=5) for r in rows)


def test_a_taken_card_is_not_touched(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = Stream(probability=0.2)
    _predictors(monkeypatch, {"FIRE_RISK": stream})
    pipeline_run_module.run(engine(), at=NOW)
    with engine().begin() as conn:
        conn.execute(
            prediction.update()
            .where(prediction.c.facility_id == "F-1")
            .values(assignee="dispatcher")
        )

    stream.probability = 0.8
    pipeline_run_module.run(engine(), at=NOW + timedelta(minutes=5))

    taken, free = _rows("FIRE_RISK")
    assert taken.level == "MEDIUM" and taken.probability == 0.2
    assert free.level == "HIGH"


def test_later_runs_of_the_day_take_only_the_candidates(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = Stream(chosen={"F-2"})
    _predictors(monkeypatch, {"FIRE_RISK": stream})

    pipeline_run_module.run(engine(), at=NOW)
    assert sorted(stream.seen) == ["F-1", "F-2"]

    stream.seen.clear()
    pipeline_run_module.run(engine(), at=NOW + timedelta(minutes=1))
    assert stream.seen == ["F-2"]


def test_the_run_records_the_stream_lag(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _predictors(monkeypatch, {"FIRE_RISK": Stream()})
    pipeline_run_module.run(engine(), at=NOW - timedelta(minutes=1))
    with engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO alarm_event (sensor_id, facility_id, occurred_at, alarm_type, "
                "received_at) VALUES ('C-1', 'F-1', :occurred, 'SMOKE_DETECTED', :received)"
            ),
            {"occurred": NOW - timedelta(seconds=40), "received": NOW - timedelta(seconds=30)},
        )

    result = pipeline_run_module.run(engine(), at=NOW)

    assert result.stream_events == 1
    assert result.stream_lag_ms is not None and result.stream_lag_ms >= 40_000
    with engine().connect() as conn:
        row = conn.execute(select(pipeline_run).where(pipeline_run.c.id == result.run_id)).one()
    assert row.stream_events == 1
    assert row.stream_lag_ms == result.stream_lag_ms
