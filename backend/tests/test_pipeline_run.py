"""Конвейер прогноза. Спецификация §7, §9.

Проверяется обход объектов и направлений, снимок признаков в
`prediction.features`, идемпотентность прогона и пропуск направления без
модели без падения всего прогона.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
    """Диспетчер взял прогноз по F-1: следующие сутки F-1 не трогаются, а
    нетронутая карточка F-2 переписывается свежим прогнозом. Работа по F-1
    кончилась, и F-1 получает новую карточку. ADR 0020, ADR 0022."""
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})
    pipeline_run_module.run(engine(), at=NOW)
    with engine().begin() as conn:
        conn.execute(
            update(prediction).where(prediction.c.facility_id == "F-1").values(status="IN_REVIEW")
        )

    next_day = NOW + timedelta(days=1)
    pipeline_run_module.run(engine(), at=next_day)

    with engine().connect() as conn:
        rows = {(row.facility_id, row.status): row for row in conn.execute(select(prediction))}
    assert set(rows) == {("F-1", "IN_REVIEW"), ("F-2", "NEW")}
    assert rows[("F-2", "NEW")].computed_at_bucket == pipeline_run_module.day_start(next_day)

    with engine().begin() as conn:
        conn.execute(
            update(prediction).where(prediction.c.facility_id == "F-1").values(status="DECIDED")
        )
    after = pipeline_run_module.run(engine(), at=NOW + timedelta(days=2))
    # Новая карточка F-1 и новые сутки в карточке F-2.
    assert after.prediction_count == 2
    with engine().connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM prediction")).scalar_one()
    assert count == 3


def test_an_untouched_card_follows_the_fresh_forecast(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одна открытая карточка на объект: нетронутая переписывается. ADR 0022."""
    predictor = FakePredictor("SENSOR_FAILURE", probability=0.9)
    _predictors(monkeypatch, {"SENSOR_FAILURE": predictor})
    pipeline_run_module.run(engine(), at=NOW)
    predictor.probability = 0.2

    pipeline_run_module.run(engine(), at=NOW + timedelta(days=1))

    with engine().connect() as conn:
        rows = list(conn.execute(select(prediction).where(prediction.c.facility_id == "F-1")))
    assert len(rows) == 1
    assert rows[0].probability == pytest.approx(0.2)


# --- заморозка законченной карточки, ADR 0021 --------------------------------


class IncidentPredictor(FakePredictor):
    """Потоковое направление с происшествием и моментом последнего события."""

    def __init__(self, code: str, started: datetime) -> None:
        super().__init__(code)
        self.started = started
        self.event_at = started

    def incident(self, features: FeatureVector, at: datetime) -> datetime | None:
        return self.started

    def last_event(self, features: FeatureVector, at: datetime) -> datetime | None:
        return self.event_at


def _finish(card_id: str) -> None:
    with engine().begin() as conn:
        conn.execute(
            update(prediction)
            .where(prediction.c.id == card_id)
            .values(status="DECIDED", verdict="AGREED", dispatcher_level="HIGH")
        )


def _rows() -> list[Any]:
    with engine().connect() as conn:
        return list(
            conn.execute(
                select(prediction)
                .where(prediction.c.facility_id == "F-1")
                .order_by(prediction.c.computed_at_bucket)
            )
        )


def test_a_finished_card_stays_frozen_without_new_events(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Закрытое происшествие не всплывает снова, пока нет новых событий."""
    fire = IncidentPredictor("FIRE_RISK", started=NOW - timedelta(minutes=30))
    _predictors(monkeypatch, {"FIRE_RISK": fire})
    pipeline_run_module.run(engine(), at=NOW)
    frozen = _rows()[0]
    _finish(frozen.id)

    pipeline_run_module.run(engine(), at=NOW + timedelta(minutes=1))

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].computed_at == frozen.computed_at
    assert rows[0].blocks == frozen.blocks
    assert rows[0].status == "DECIDED"


def test_new_events_go_to_a_clean_copy_of_a_finished_card(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Решение остаётся в замороженной карточке, новые данные идут в копию."""
    fire = IncidentPredictor("FIRE_RISK", started=NOW - timedelta(minutes=30))
    _predictors(monkeypatch, {"FIRE_RISK": fire})
    pipeline_run_module.run(engine(), at=NOW)
    frozen = _rows()[0]
    _finish(frozen.id)

    fire.event_at = NOW + timedelta(minutes=2)
    later = NOW + timedelta(minutes=3)
    pipeline_run_module.run(engine(), at=later)
    pipeline_run_module.run(engine(), at=later + timedelta(minutes=1))

    rows = _rows()
    assert [row.status for row in rows] == ["DECIDED", "NEW"]
    assert rows[0].computed_at == frozen.computed_at
    assert rows[0].verdict == "AGREED"
    # Второй прогон пишет в ту же копию, а не плодит третью.
    assert rows[1].computed_at_bucket == later


# --- журнал всех прогнозов, ADR 0022 ------------------------------------------


def test_the_forecast_log_keeps_every_forecast_when_enabled(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Карточка переписывается, а каждый прогноз остаётся строкой в файле."""
    path = tmp_path / "forecasts.jsonl"
    monkeypatch.setattr(
        pipeline_run_module,
        "config",
        dataclasses.replace(
            pipeline_run_module.config, forecast_log=True, forecast_log_path=str(path)
        ),
    )
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})

    pipeline_run_module.run(engine(), at=NOW)
    pipeline_run_module.run(engine(), at=NOW + timedelta(days=1))

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 4
    assert {r["facilityId"] for r in records} == {"F-1", "F-2"}
    assert records[0]["direction"] == "SENSOR_FAILURE"
    assert records[0]["level"] == "CRITICAL"


def test_the_forecast_log_is_off_by_default(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "forecasts.jsonl"
    monkeypatch.setattr(
        pipeline_run_module,
        "config",
        dataclasses.replace(pipeline_run_module.config, forecast_log_path=str(path)),
    )
    _predictors(monkeypatch, {"SENSOR_FAILURE": FakePredictor("SENSOR_FAILURE")})

    pipeline_run_module.run(engine(), at=NOW)

    assert not path.exists()
