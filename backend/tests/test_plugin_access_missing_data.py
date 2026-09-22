"""Устойчивость плагина доступа к отсутствию данных.

Два случая, которые конвейер обязан пережить без исключения: единица без
единой сработки за 720 часов и единица, которой нет в справочнике объектов
(`facility`). `build_features` не читает таблицу `facility` вообще — она
только фильтрует `alarm_event` по `facility_id` — поэтому оба случая обязаны
дать нулевой, а не пустой вектор признаков, и предиктор обязан отдать
вероятность и объяснение, а не упасть.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.ml.plugins import unauthorized_access
from app.ml.plugins.unauthorized_access import FEATURE_LABELS, UnauthorizedAccess
from app.ml.protocol import FeatureContext
from app.tables import alarm_event, facility

AT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
FEATURE_NAMES = list(FEATURE_LABELS)


@pytest.fixture
def db() -> Iterator[None]:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as conn:
        conn.execute(delete(alarm_event))
        conn.execute(delete(facility))

    yield

    with engine().begin() as conn:
        conn.execute(delete(alarm_event))
        conn.execute(delete(facility))


class StubModel:
    """Заглушка бустера: порядок столбцов и фиксированная вероятность."""

    def feature_name(self) -> list[str]:
        return FEATURE_NAMES

    def predict(self, rows: list[list[float]]) -> list[float]:
        return [0.42 for _ in rows]


class FakeTreeExplainer:
    """Заглушка `shap.TreeExplainer`: детерминированный вклад по номеру столбца."""

    def __init__(self, model: StubModel) -> None:
        self.model = model

    def shap_values(self, rows: np.ndarray) -> np.ndarray:
        n_features = rows.shape[1]
        base = np.arange(n_features, dtype=float) * 0.3
        signs = np.where(np.arange(n_features) % 2 == 0, 1.0, -1.0)
        return np.tile((base * signs), (rows.shape[0], 1))


@pytest.fixture
def fake_shap(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("shap")
    module.TreeExplainer = FakeTreeExplainer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shap", module)


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> StubModel:
    model = StubModel()
    monkeypatch.setattr(unauthorized_access, "load_model", lambda direction: model)
    return model


_NO_HISTORY_KEYS = (
    "n_alarms_1h",
    "n_alarms_24h",
    "n_alarms_168h",
    "n_alarms_720h",
    "alarm_hour_share_720h",
    "hours_since_last_alarm",
    "night_share",
    "neighbor_channels_1h",
    "is_disarmed",
    "has_access_sequence",
    "n_armed_alarms_24h",
    "n_armed_alarms_168h",
    "hours_since_last_armed_alarm",
)


def _assert_no_alarm_history(features: dict[str, float]) -> None:
    for name in _NO_HISTORY_KEYS:
        assert features[name] == 0.0, f"{name} обязан быть 0.0 без единой записи в alarm_event"


def _assert_valid_forecast(plugin: UnauthorizedAccess, features: dict[str, float]) -> None:
    probability = plugin.predict(features)
    assert isinstance(probability, float)
    assert 0.0 <= probability <= 1.0

    blocks = plugin.explain(features, AT)
    types_ = [block.type for block in blocks]
    assert "factors" in types_
    assert "timeseries" in types_
    assert "timeline" in types_
    factors = next(block for block in blocks if block.type == "factors")
    assert factors.data["items"]
    for item in factors.data["items"]:
        assert -1.0 <= item["weight"] <= 1.0


@pytest.mark.usefixtures("db")
def test_unit_without_a_single_alarm_in_720h(stub_model: StubModel, fake_shap: None) -> None:
    fid = "F-ACC-NO-ALARMS"
    with engine().begin() as conn:
        conn.execute(
            facility.insert(),
            {
                "id": fid,
                "collector": "K-TEST",
                "district": "CAO",
                "address": "тест",
                "lat": 0.0,
                "lon": 0.0,
                "facility_type": "chamber",
                "is_active": True,
            },
        )

    with engine().connect() as conn:
        features = unauthorized_access.UnauthorizedAccess().build_features(
            FeatureContext(conn=conn, facility_id=fid, at=AT)
        )

    _assert_no_alarm_history(features)
    _assert_valid_forecast(UnauthorizedAccess(), features)


@pytest.mark.usefixtures("db")
def test_unit_missing_from_the_facility_catalog(stub_model: StubModel, fake_shap: None) -> None:
    fid = "F-ACC-UNKNOWN"
    with engine().connect() as conn:
        features = unauthorized_access.UnauthorizedAccess().build_features(
            FeatureContext(conn=conn, facility_id=fid, at=AT)
        )

    _assert_no_alarm_history(features)
    _assert_valid_forecast(UnauthorizedAccess(), features)
