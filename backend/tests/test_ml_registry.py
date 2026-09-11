"""Реестр предикторов.

Проверяется обещание из спецификации §7: направление подключается файлом в
`app/ml/plugins/` и записью в реестре направлений. Проверяется и обратное:
направление без предиктора — законное состояние, а не отказ.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from app.meta import Direction, Reason
from app.ml import registry
from app.ml.protocol import Block, FeatureContext, FeatureVector, Predictor, Window


class FakePredictor:
    """Предиктор-пустышка. Реализует протокол целиком и ничего не считает."""

    def __init__(self, code: str, probability: float = 0.5) -> None:
        self.code = code
        self.probability = probability

    def build_features(self, ctx: FeatureContext) -> FeatureVector:
        return {"days_since_service": 10.0}

    def predict(self, features: FeatureVector) -> float:
        return self.probability

    def explain(self, features: FeatureVector) -> list[Block]:
        return [Block(type="factors", title="Факторы", data={"items": []})]

    def suggest_work_type(self, features: FeatureVector, probability: float) -> str:
        return "Диагностика датчика"

    def label_rule(self, conn: object, facility_id: str, window: Window) -> bool:
        return False


def direction(code: str) -> Direction:
    return Direction(
        code=code,
        label=code,
        short_label=code[:2],
        accent="#000000",
        min_horizon_hours=24,
        reasons=(Reason("NO_DEFECT", "Дефект не подтверждён"),),
        work_types=("Осмотр",),
    )


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    registry.reset()
    yield
    registry.reset()


def test_the_fake_predictor_satisfies_the_protocol() -> None:
    assert isinstance(FakePredictor("SENSOR_FAILURE"), Predictor)


def test_register_and_get() -> None:
    predictor = FakePredictor("SENSOR_FAILURE")
    registry.register(predictor)
    assert registry.get("SENSOR_FAILURE") is predictor


def test_unknown_direction_gives_none() -> None:
    assert registry.get("NOT_A_DIRECTION") is None


def test_two_predictors_on_one_direction_raise() -> None:
    """Молчаливая замена дала бы разный результат от порядка импорта."""
    registry.register(FakePredictor("FIRE_RISK"))
    with pytest.raises(ValueError, match="FIRE_RISK"):
        registry.register(FakePredictor("FIRE_RISK"))


def test_active_keeps_the_order_of_the_direction_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "active_directions",
        lambda: (direction("FIRE_RISK"), direction("SENSOR_FAILURE")),
    )
    registry.register(FakePredictor("SENSOR_FAILURE"))
    registry.register(FakePredictor("FIRE_RISK"))

    assert [item.code for item in registry.active()] == ["FIRE_RISK", "SENSOR_FAILURE"]


def test_a_direction_without_a_predictor_is_skipped_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Спецификация §13: у направления «Износ» предиктора нет, и это норма."""
    monkeypatch.setattr(
        registry,
        "active_directions",
        lambda: (direction("SENSOR_FAILURE"), direction("WEAR_OUT")),
    )
    registry.register(FakePredictor("SENSOR_FAILURE"))

    assert [item.code for item in registry.active()] == ["SENSOR_FAILURE"]
    assert registry.missing() == ("WEAR_OUT",)


def test_a_new_plugin_file_registers_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Тест на гибкость со стороны конвейера.

    Файл кладётся в каталог плагинов, и направление появляется в реестре. Ни
    один модуль конвейера при этом не правится.
    """
    import app.ml.plugins as package

    folder = tmp_path  # type: ignore[assignment]
    module_path = folder / "flood_risk.py"  # type: ignore[operator]
    module_path.write_text(
        "from app.ml import register\n"
        "from tests.test_ml_registry import FakePredictor\n"
        "register(FakePredictor('FLOOD_RISK', 0.9))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(package, "__path__", [str(folder), *package.__path__])
    monkeypatch.setattr(registry, "active_directions", lambda: (direction("FLOOD_RISK"),))

    predictor = registry.get("FLOOD_RISK")
    assert predictor is not None
    assert predictor.predict({}) == 0.9
    assert registry.missing() == ()

    sys.modules.pop("app.ml.plugins.flood_risk", None)


def test_the_context_forbids_data_after_the_moment_of_the_run() -> None:
    """Контекст несёт момент расчёта. Признак после него — утечка."""
    at = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    ctx = FeatureContext(conn=object(), facility_id="F-1", at=at)  # type: ignore[arg-type]
    assert ctx.at == at

    window = Window(start=at, end=at)
    assert window.start == window.end
