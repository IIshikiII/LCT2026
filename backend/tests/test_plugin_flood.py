"""Плагин направления «риск подтопления». ADR 0012, ADR 0013.

Часть проверок идёт на настоящей модели из `artifacts/flood_risk/`. Каталог
артефактов в git не хранится, поэтому без файла модели эти проверки
пропускаются, а не падают. Положить модель: `ml/flood/07_export.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, insert, text

from app.db import engine
from app.features import flood, flood_water, registry
from app.meta import directions
from app.meta.catalog import level_for
from app.ml.plugins import flood_risk
from app.ml.plugins.flood_risk import FEATURE_LABELS, FloodRisk, budget_bands
from app.ml.protocol import Predictor, Window, applies
from app.tables import alarm_event, facility, sensor
from tests.conftest import reset_database

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "flood_risk"
AT = datetime(2026, 5, 10, 0, 0, tzinfo=UTC)


def _real_model() -> Any:
    if not (ARTIFACTS / "latest.joblib").exists():
        pytest.skip("модели подтопления нет в artifacts/flood_risk")
    import joblib

    return joblib.load(ARTIFACTS / "latest.joblib")


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Any:
    real = _real_model()
    monkeypatch.setattr(flood_risk, "load_model", lambda direction: real)
    flood_risk.reset_cache()
    yield real
    flood_risk.reset_cache()


def _vector(model: Any, **values: float) -> dict[str, float]:
    features = {name: 0.0 for name in model.feature_name()}
    features.update(values)
    return features


def test_the_plugin_implements_the_protocol() -> None:
    assert isinstance(FloodRisk(), Predictor)
    assert FloodRisk().code == directions.FLOOD_RISK.code


def test_every_model_feature_has_a_builder_and_a_human_label(model: Any) -> None:
    names = list(model.feature_name())
    assert registry.missing(flood.DIRECTION, names) == []
    unlabelled = [name for name in names if name not in FEATURE_LABELS]
    assert unlabelled == [], f"признаки без подписи: {unlabelled}"


def test_the_card_speaks_about_the_pump_in_words(model: Any) -> None:
    """Карточка называет насос словами, а не именем столбца."""
    features = _vector(
        model,
        pump_changes_24h=180.0,
        pump_blink_hours_24h=3.0,
        obj_pump_blink_24h=2.0,
        evt_hours_168h=4.0,
        evt_hours_8760h=40.0,
        evt_hours_since_last=30.0,
        unit_pumps=2.0,
    )
    blocks = FloodRisk().explain(features, AT)
    kinds = [block.type for block in blocks]
    assert kinds == ["factors", "timeseries", "timeline"]

    factors = blocks[0].data["items"]
    assert all(-1.0 <= item["weight"] <= 1.0 for item in factors)
    assert all("_" not in item["label"] for item in factors)

    titles = [event["title"] for event in blocks[2].data["events"]]
    assert "Насос мигал в последние сутки" in titles
    assert "Беда у соседа по объекту" in titles


def test_the_model_is_the_ensemble_and_explains_itself(model: Any) -> None:
    """SHAP ансамбля плюс фон равны логиту прогноза: объяснение точное."""
    import math

    import numpy as np

    assert hasattr(model, "shap_values")
    features = _vector(model, evt_hours_24h=3.0, evt_hours_168h=5.0, weather_precip_72h=30.0)
    row = np.asarray([[features[name] for name in model.feature_name()]], dtype=float)
    total = float(model.shap_values(row)[0].sum()) + model.expected_value
    probability = FloodRisk().predict(features)
    assert total == pytest.approx(math.log(probability / (1 - probability)), abs=1e-4)


def test_water_yesterday_raises_the_risk(model: Any) -> None:
    """Самый сильный сигнал модели ADR 0012: вода была в прошлые сутки."""
    dry = _vector(model, unit_pumps=2.0, evt_hours_since_last=2000.0, unit_age_days=900.0)
    wet = {**dry, "evt_hours_24h": 4.0, "evt_hours_168h": 6.0, "evt_hours_since_last": 3.0}
    plugin = FloodRisk()
    assert plugin.predict(wet) > plugin.predict(dry)


def test_a_pump_working_harder_than_usual_raises_the_risk(model: Any) -> None:
    """Насос откачивает больше обычного значит вода идёт.

    Мигание насоса само по себе модель ADR 0012 почти не читает: после того
    как проверки ушли из метки, у мигания не осталось своего сигнала (ADR 0013).
    Насосный сигнал модели это рост работы насоса.
    """
    calm = _vector(model, unit_pumps=2.0, evt_hours_since_last=2000.0, unit_age_days=900.0)
    pumping = {**calm, "pump_on_growth_24_720": 5.0, "pump_on_share_24h": 0.5}
    plugin = FloodRisk()
    assert plugin.predict(pumping) > plugin.predict(calm)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"evt_hours_24h": 2.0}, "Откачка воды"),
        ({"pump_blink_hours_24h": 1.0}, "Проверка приямка и насоса"),
        ({"pump_unavailable_24h": 1.0}, "Проверка приямка и насоса"),
        ({}, "Гидроизоляция"),
    ],
)
def test_the_work_type_follows_the_signal(values: dict[str, float], expected: str) -> None:
    work_type = FloodRisk().suggest_work_type(values, 0.5)
    assert work_type == expected
    assert work_type in directions.FLOOD_RISK.work_types


def test_the_bands_equal_the_measured_levels() -> None:
    """Границы уровней и порог заявки взяты из замера модели, одним числом."""
    path = ARTIFACTS / "metrics.json"
    if not path.exists():
        pytest.skip("замера модели подтопления нет в artifacts/flood_risk")
    decision = json.loads(path.read_text(encoding="utf-8"))["decision"]
    bands = dict(directions.FLOOD_RISK.level_thresholds)
    assert bands == pytest.approx({k: float(v) for k, v in decision["levels"].items()})
    assert decision["scale"] == "raw"
    assert float(decision["threshold"]) == pytest.approx(bands["HIGH"])
    assert level_for(bands["HIGH"], directions.FLOOD_RISK) == "HIGH"
    assert level_for(bands["HIGH"] - 1e-6, directions.FLOOD_RISK) == "MEDIUM"
    assert decision["alert_budget"]["window_days"] == flood_risk.BUDGET_WINDOW_DAYS


def test_the_published_point_beats_the_naive_rule() -> None:
    """Рабочая точка это скользящий бюджет, и она выше правила по обеим мерам."""
    path = ARTIFACTS / "metrics.json"
    if not path.exists():
        pytest.skip("замера модели подтопления нет в artifacts/flood_risk")
    payload = json.loads(path.read_text(encoding="utf-8"))
    chosen, targets = payload["decision"]["chosen"], payload["targets"]
    assert payload["decision"]["outcome"] == "rolling_budget"
    assert chosen["cell_precision"] > targets["precision"]
    assert chosen["cell_recall"] > targets["recall"]


# --- скользящий бюджет тревог ---

STATIC = directions.FLOOD_RISK.level_thresholds


def test_the_budget_takes_as_many_alerts_as_the_naive_rule() -> None:
    history = [(0.9, 0.0), (0.5, 2.0), (0.2, 0.0), (0.1, 1.0)]
    fresh = [(0.4, 0.0), (0.05, 0.0)]
    bands = budget_bands(history, fresh, STATIC)
    assert bands is not None
    high = dict(bands)["HIGH"]
    # Правило подняло две тревоги, значит HIGH это вторая вероятность окна.
    assert high == 0.5
    alerts = [
        p
        for p, _ in [*history, *fresh]
        if level_for(p, directions.FLOOD_RISK, bands) in directions.FLOOD_RISK.order_levels
    ]
    assert len(alerts) == 2


def test_the_budget_keeps_the_bands_ordered() -> None:
    bands = budget_bands([(0.95, 1.0), (0.9, 1.0)], [], STATIC)
    assert bands is not None
    values = [bound for _, bound in bands]
    assert values == sorted(values)
    assert dict(bands)["CRITICAL"] >= dict(bands)["HIGH"]


def test_no_water_for_a_month_falls_back_to_the_static_bands() -> None:
    assert budget_bands([(0.9, 0.0)], [(0.3, 0.0)], STATIC) is None


# --- правило метки и отбор объектов, на базе ---


@pytest.fixture
def db() -> Any:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 — база недоступна это пропуск, не падение
        pytest.skip(f"тестовая база недоступна: {error}")
    with engine().begin() as conn:
        reset_database(conn)
        conn.execute(
            insert(facility),
            [
                {
                    "id": code,
                    "collector": "K-1",
                    "district": "SAO",
                    "address": code,
                    "lat": 0.0,
                    "lon": 0.0,
                    "facility_type": "chamber",
                    "is_active": True,
                }
                for code in ("F-PUMP", "F-DRY", "F-EVENTS")
            ],
        )
        conn.execute(
            insert(sensor),
            [{"id": "F-PUMP-P", "facility_id": "F-PUMP", "sensor_type": flood.SENSOR_PUMP}],
        )
        conn.execute(
            insert(alarm_event),
            [
                {
                    "id": 1,
                    "facility_id": "F-EVENTS",
                    "sensor_id": "F-EVENTS-S",
                    "alarm_type": flood.FLOOD_SENSOR_OPEN,
                    "occurred_at": AT + timedelta(hours=3),
                },
                {
                    "id": 3,
                    "facility_id": "F-PUMP",
                    "sensor_id": "F-PUMP-P",
                    "alarm_type": flood.FLOODED,
                    # Вторник, 11:00 по Москве: плановая проверка, не вода.
                    "occurred_at": datetime(2026, 5, 12, 8, 0, tzinfo=UTC),
                },
                {
                    "id": 2,
                    "facility_id": "F-DRY",
                    "sensor_id": "F-DRY-DOOR",
                    "alarm_type": "DOOR_OPEN",
                    "occurred_at": AT + timedelta(hours=3),
                },
            ],
        )
    # Классификатора нет, работает правило времени: воскресная ночь это вода,
    # вторник днём это проверка.
    original = flood_water.classifier
    flood_water.classifier = lambda: None  # type: ignore[assignment]
    with engine().begin() as conn:
        flood_water.refresh(conn, AT + timedelta(days=4))
    flood_water.classifier = original  # type: ignore[assignment]
    yield
    with engine().begin() as conn:
        conn.execute(delete(alarm_event))
        reset_database(conn)


@pytest.mark.usefixtures("db")
def test_the_direction_applies_only_where_water_can_be_seen() -> None:
    plugin = FloodRisk()
    with engine().connect() as conn:
        assert applies(plugin, conn, "F-PUMP")
        assert applies(plugin, conn, "F-EVENTS")
        assert not applies(plugin, conn, "F-DRY")


@pytest.mark.usefixtures("db")
def test_the_label_rule_repeats_the_training_label() -> None:
    plugin = FloodRisk()
    ahead = Window(start=AT, end=AT + timedelta(hours=24))
    with engine().connect() as conn:
        assert plugin.label_rule(conn, "F-EVENTS", ahead)
        # Чужой тип тревоги подтоплением не является.
        assert not plugin.label_rule(conn, "F-DRY", ahead)
        # Сутки с сигналом только днём в будни разметка зовёт проверкой (ADR 0012).
        workday = Window(start=AT + timedelta(days=2), end=AT + timedelta(days=3))
        assert not plugin.label_rule(conn, "F-PUMP", workday)
        # Окно открыто слева: событие ровно в момент начала окна не считается.
        exact = Window(start=AT + timedelta(hours=3), end=AT + timedelta(hours=27))
        assert not plugin.label_rule(conn, "F-EVENTS", exact)
