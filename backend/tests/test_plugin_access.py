"""Протокол плагина направления доступа. Спецификация §7.

Прогнать `predict`/`explain` на настоящем бустере LightGBM и настоящем
`shap.TreeExplainer` этот файл не может: набор `ml` пока не держит ни одну из
двух библиотек (см. `docs/08-ml-plugin.md`, задача T29). Вместо этого модель и
`shap` подменяются заглушками с тем же интерфейсом — `feature_name()`,
`predict()`, `TreeExplainer.shap_values()` — так протокол проверяется целиком,
а численные правила блоков остаются за `test_plugin_access_explain.py`.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime

import numpy as np
import pytest

from app.ml.plugins import unauthorized_access
from app.ml.plugins.unauthorized_access import FEATURE_LABELS, UnauthorizedAccess
from app.ml.protocol import Predictor

AT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
FEATURE_NAMES = list(FEATURE_LABELS)

FEATURES: dict[str, float] = {name: 0.0 for name in FEATURE_NAMES}
FEATURES.update(
    {
        "n_alarms_1h": 2.0,
        "n_alarms_24h": 5.0,
        "n_alarms_168h": 11.0,
        "n_alarms_720h": 34.0,
        "hours_since_last_alarm": 0.0,
        "night_share": 0.4,
        "hour_of_day": 22.0,
        "day_of_week": 4.0,
        "month": 9.0,
        "n_armed_alarms_24h": 1.0,
        "n_armed_alarms_168h": 7.0,
        "hours_since_last_armed_alarm": 6.0,
    }
)


class StubModel:
    """Заглушка бустера: порядок столбцов и фиксированная вероятность."""

    def __init__(self, probability: float = 0.73) -> None:
        self.probability = probability

    def feature_name(self) -> list[str]:
        return FEATURE_NAMES

    def predict(self, rows: list[list[float]]) -> list[float]:
        return [self.probability for _ in rows]


class FakeTreeExplainer:
    """Заглушка `shap.TreeExplainer`: детерминированный вклад по номеру столбца."""

    def __init__(self, model: StubModel) -> None:
        self.model = model

    def shap_values(self, rows: np.ndarray) -> np.ndarray:
        n_features = rows.shape[1]
        # Знак чередуется, величина растёт со столбцом — довольно, чтобы
        # проверить и диапазон, и повторяемость вызова.
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


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(unauthorized_access, "load_model", lambda direction: None)


class TestProtocol:
    def test_plugin_implements_predictor(self) -> None:
        assert isinstance(UnauthorizedAccess(), Predictor)

    def test_plugin_code_is_the_direction(self) -> None:
        assert UnauthorizedAccess().code == unauthorized_access.DIRECTION


class TestPredict:
    def test_predict_returns_number_between_zero_and_one(self, stub_model: StubModel) -> None:
        result = UnauthorizedAccess().predict(FEATURES)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_predict_uses_model_column_order(self, stub_model: StubModel) -> None:
        # Заглушка обучена на FEATURE_NAMES: перестановка признаков во входном
        # словаре не имеет значения, `predict` обязан читать порядок у модели.
        shuffled = dict(reversed(list(FEATURES.items())))
        assert UnauthorizedAccess().predict(shuffled) == UnauthorizedAccess().predict(FEATURES)

    def test_predict_raises_without_model(self, no_model: None) -> None:
        with pytest.raises(RuntimeError):
            UnauthorizedAccess().predict(FEATURES)

    def test_two_calls_on_same_input_agree(self, stub_model: StubModel) -> None:
        plugin = UnauthorizedAccess()
        assert plugin.predict(FEATURES) == plugin.predict(FEATURES)


class TestExplain:
    def test_explain_always_returns_factors_and_timeseries(
        self, stub_model: StubModel, fake_shap: None
    ) -> None:
        blocks = UnauthorizedAccess().explain(FEATURES, AT)
        types_ = [block.type for block in blocks]
        assert "factors" in types_
        assert "timeseries" in types_

    def test_explain_weights_stay_inside_range(
        self, stub_model: StubModel, fake_shap: None
    ) -> None:
        blocks = UnauthorizedAccess().explain(FEATURES, AT)
        factors = next(block for block in blocks if block.type == "factors")
        for item in factors.data["items"]:
            assert -1.0 <= item["weight"] <= 1.0

    def test_explain_raises_without_model(self, no_model: None, fake_shap: None) -> None:
        with pytest.raises(RuntimeError):
            UnauthorizedAccess().explain(FEATURES, AT)

    def test_two_calls_on_same_input_give_same_blocks(
        self, stub_model: StubModel, fake_shap: None
    ) -> None:
        plugin = UnauthorizedAccess()
        first = [block.as_dict() for block in plugin.explain(FEATURES, AT)]
        second = [block.as_dict() for block in plugin.explain(FEATURES, AT)]
        assert first == second
