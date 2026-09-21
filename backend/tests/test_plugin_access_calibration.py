"""Калибровка вероятности плагина доступа. Задача T30.

`predict` читает `calibration.joblib` рядом с моделью через
`app.ml.tracking.load_calibrator` и подменяет сырую вероятность бустера
откалиброванной. Файла нет значит калибровки нет: метод отдаёт сырую
вероятность, а не падает (T28 обучает калибратор, но раньше его никто не
читал).
"""

from __future__ import annotations

import pytest

from app.ml.plugins import unauthorized_access
from app.ml.plugins.unauthorized_access import UnauthorizedAccess

FEATURES: dict[str, float] = {"n_alarms_24h": 3.0, "hours_since_last_alarm": 2.0}


class StubModel:
    def feature_name(self) -> list[str]:
        return list(FEATURES)

    def predict(self, rows: list[list[float]]) -> list[float]:
        return [0.73 for _ in rows]


class StubCalibrator:
    """Заглушка изотонической регрессии: делит вероятность пополам."""

    def predict(self, values: list[float]) -> list[float]:
        return [value / 2.0 for value in values]


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> StubModel:
    model = StubModel()
    monkeypatch.setattr(unauthorized_access, "load_model", lambda direction: model)
    return model


def test_predict_applies_calibrator_when_file_is_present(
    stub_model: StubModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unauthorized_access, "load_calibrator", lambda direction: StubCalibrator())

    result = UnauthorizedAccess().predict(FEATURES)

    assert result == pytest.approx(0.365)
    assert result != pytest.approx(0.73)


def test_predict_returns_raw_probability_without_calibrator_file(
    stub_model: StubModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unauthorized_access, "load_calibrator", lambda direction: None)

    result = UnauthorizedAccess().predict(FEATURES)

    assert result == pytest.approx(0.73)
