"""Плагин пожарного риска на синтетике посева. ADR 0016.

Посев кладёт свежие эпизоды по одному на уровень (`app/synth/fire.py`).
Прогон конвейера обязан поставить им уровни экспертных правил, собрать
карточку с объяснением и не поднять тревогу там, где её быть не должно:
на пачке обхода и на газе внутри окна ППР.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.main import app
from app.meta import directions
from app.ml.plugins.fire_risk import ACCURACY_NOTE, FireRisk
from app.ml.protocol import Predictor
from app.pipeline import run as pipeline
from app.synth.generate import generate
from app.tables import prediction, sensor
from tests.conftest import reset_database

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
API_PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def ran() -> Iterator[dict[str, dict]]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")
    migrate.run()
    with engine().begin() as conn:
        reset_database(conn)
        generate(conn, seed=20260101, facility_count=60, now=NOW)
    # Прогон только пожара: другие направления здесь не проверяются, а их
    # модели в каталоге артефактов есть не у каждой машины. Предиктор
    # подставлен прямо: `registry.reset()` в других файлах снимает его из
    # реестра, а повторный импорт модуль не выполняет (`test_plugin_contract.py`).
    predictor = FireRisk()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pipeline, "active_directions", lambda: (directions.FIRE_RISK,))
        patch.setattr(pipeline, "get_predictor", lambda code: predictor)
        pipeline.run(engine(), at=NOW)
    with engine().connect() as conn:
        rows = conn.execute(
            select(prediction).where(prediction.c.direction == directions.FIRE_RISK.code)
        ).all()
    yield {row.facility_id: row._asdict() for row in rows}
    with engine().begin() as conn:
        reset_database(conn)


def _facility_with_sensor(suffix: str) -> str:
    with engine().connect() as conn:
        found = conn.execute(select(sensor.c.facility_id).where(sensor.c.id.like(f"%{suffix}")))
        return str(found.scalar_one())


def test_the_plugin_implements_the_protocol() -> None:
    assert isinstance(FireRisk(), Predictor)


def test_every_level_is_shown_by_the_demo_episodes(ran: dict[str, dict]) -> None:
    levels = {row["level"] for row in ran.values()}
    assert {"LOW", "MEDIUM", "HIGH", "CRITICAL"} <= levels


def test_smoke_confirmed_by_heat_is_critical(ran: dict[str, dict]) -> None:
    critical = [row for row in ran.values() if row["level"] == "CRITICAL"]
    assert critical
    assert all(row["summary"].startswith("Пожарный риск: признаки пожара") for row in critical)


def test_a_new_sensor_is_in_its_burn_in_period(ran: dict[str, dict]) -> None:
    row = ran[_facility_with_sensor("-SMOKE-NEW")]
    assert row["level"] == "LOW"
    rules = next(block for block in row["blocks"] if block["type"] == "keyvalue")
    why = next(item["value"] for item in rules["data"]["items"] if item["label"] == "Почему")
    assert "охлаждения" in why


def test_methane_inside_a_maintenance_window_raises_nothing(ran: dict[str, dict]) -> None:
    row = ran[_facility_with_sensor("-CH4")]
    assert row["features"]["gas_pct"] == 0.0
    assert row["level"] == "LOW"


def test_every_card_has_factors_and_a_timeseries(ran: dict[str, dict]) -> None:
    for row in ran.values():
        kinds = {block["type"] for block in row["blocks"]}
        assert {"factors", "timeseries"} <= kinds


def test_the_card_says_the_accuracy_is_not_measured(ran: dict[str, dict]) -> None:
    row = next(iter(ran.values()))
    rules = next(block for block in row["blocks"] if block["type"] == "keyvalue")
    values = [item["value"] for item in rules["data"]["items"]]
    assert ACCURACY_NOTE in values


def test_the_work_type_belongs_to_the_direction(ran: dict[str, dict]) -> None:
    for row in ran.values():
        work = FireRisk().suggest_work_type(row["features"], row["probability"])
        assert work in directions.FIRE_RISK.work_types


def test_the_metrics_widget_gets_a_row_without_numbers(ran: dict[str, dict]) -> None:
    del ran
    client = TestClient(app)
    body = client.get(f"{API_PREFIX}/metrics/models").json()
    fire = next(item for item in body if item["direction"] == directions.FIRE_RISK.code)
    assert fire["precision"] is None
    assert fire["recall"] is None
    assert fire["method"] == "expert_rules"
    assert "не измерена" in fire["note"]
