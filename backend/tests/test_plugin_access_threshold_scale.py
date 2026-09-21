"""Шкала калибратора и границы уровней направления совпадают. Задача T37.

`level_thresholds` направления `UNAUTHORIZED_ACCESS` (ADR 0004) стоят на
калиброванной шкале, потому что `predict` применяет
`ARTIFACTS_DIR/unauthorized_access/calibration.joblib`, когда файл на месте
(T30). Файл лежит рядом с моделью с этой задачи, поэтому вход, который бустер
уверенно относит к тревоге, обязан получать уровень не ниже `HIGH` что с
калибратором, что без него, — иначе граница снова разошлась со шкалой
`prediction.probability`, как до этой задачи.
"""

from __future__ import annotations

import pytest

from app.meta import catalog, directions
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


def test_confident_prediction_reaches_high_with_calibrator(
    stub_model: StubModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unauthorized_access, "load_calibrator", lambda direction: StubCalibrator())

    probability = UnauthorizedAccess().predict(FEATURES)
    level = catalog.level_for(probability, directions.UNAUTHORIZED_ACCESS)

    assert probability == pytest.approx(0.365)
    assert level in ("HIGH", "CRITICAL")


def test_confident_prediction_reaches_high_without_calibrator(
    stub_model: StubModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unauthorized_access, "load_calibrator", lambda direction: None)

    probability = UnauthorizedAccess().predict(FEATURES)
    level = catalog.level_for(probability, directions.UNAUTHORIZED_ACCESS)

    assert probability == pytest.approx(0.73)
    assert level in ("HIGH", "CRITICAL")
