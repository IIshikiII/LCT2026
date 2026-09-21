"""Контракт предиктора. `TODO.md` §3, пункты 2, 3, 5 и 6.

Тест параметризован по реестру направлений, а не написан на одно: направление
без предиктора пропускается, направление с предиктором проходит все четыре
проверки. Пункт 4 (блоки объяснения) и половина пункта 1 (диапазон
вероятности) уже закрывает `test_plugin_access.py`. Пункт 1 целиком
(калибровка) и пункт 7 (версия в MLflow) в этот файл не входят: они требуют
настоящей отложенной выборки и настоящего реестра моделей, а не единичного
вызова протокола.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.meta import REGISTRY, Direction
from app.ml import registry
from app.ml.protocol import FeatureContext
from app.tables import alarm_event, facility

AT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
FACILITY_ID = "F-CONTRACT-TEST"

# Имена признаков, устойчивость которых уже закреплена буква в букву с
# `ml/access/features.py::FEATURE_COLUMNS` (докстринг `app/features/access.py`).
# Направление без записи здесь проверяется только на структуру словаря: тип
# ключей и значений, а не конкретный набор имён.
def _known_names(code: str) -> frozenset[str] | None:
    if code != "UNAUTHORIZED_ACCESS":
        return None
    from app.ml.plugins.unauthorized_access import FEATURE_LABELS

    return frozenset(FEATURE_LABELS)


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
        conn.execute(
            facility.insert(),
            {
                "id": FACILITY_ID,
                "collector": "K-TEST",
                "district": "CAO",
                "address": "тест",
                "lat": 0.0,
                "lon": 0.0,
                "facility_type": "chamber",
                "is_active": True,
            },
        )

    yield

    with engine().begin() as conn:
        conn.execute(delete(alarm_event))
        conn.execute(delete(facility))


def _registered_directions() -> list[tuple[Direction, Any]]:
    """Пары «направление, предиктор», собранные один раз при сборе тестов.

    Предиктор берётся не через `registry.get()` в теле теста: другие файлы
    (`test_ml_registry.py`) чистят реестр фикстурой `registry.reset()`, и
    повторный `load_plugins()` после сброса не перерегистрирует уже
    импортированные модули — Python не выполняет тело модуля дважды. Ссылка
    на экземпляр, взятая при сборе тестов, не зависит от порядка файлов.
    """
    return [(item, predictor) for item in REGISTRY if (predictor := registry.get(item.code))]


DIRECTIONS = _registered_directions()
DIRECTION_IDS = [item.code for item, _ in DIRECTIONS]


def _stub_model(monkeypatch: pytest.MonkeyPatch, predictor: Any, feature_names: list[str]) -> bool:
    """Подменяет `load_model` плагина заглушкой. Отдаёт `False`, если плагин
    не следует соглашению `load_model(direction) -> модель | None`."""
    module = importlib.import_module(type(predictor).__module__)
    if not hasattr(module, "load_model"):
        return False

    class StubModel:
        def feature_name(self) -> list[str]:
            return feature_names

        def predict(self, rows: list[list[float]]) -> list[float]:
            return [0.5 for _ in rows]

    monkeypatch.setattr(module, "load_model", lambda direction: StubModel())
    if hasattr(module, "reset_cache"):
        module.reset_cache()
    return True


@pytest.mark.skipif(not DIRECTIONS, reason="ни один предиктор не зарегистрирован")
@pytest.mark.parametrize("direction,predictor", DIRECTIONS, ids=DIRECTION_IDS)
@pytest.mark.usefixtures("db")
class TestPredictorContract:
    def test_horizon_is_at_least_24_hours(self, direction: Direction, predictor: Any) -> None:
        assert direction.min_horizon_hours >= 24

    def test_build_features_returns_named_float_vector(
        self, direction: Direction, predictor: Any
    ) -> None:
        with engine().connect() as conn:
            ctx = FeatureContext(conn=conn, facility_id=FACILITY_ID, at=AT)
            features = predictor.build_features(ctx)

        assert isinstance(features, dict)
        assert features
        for name, value in features.items():
            assert isinstance(name, str) and name
            assert isinstance(value, float)

        expected = _known_names(direction.code)
        if expected is not None:
            assert set(features) == expected

    def test_suggest_work_type_is_a_known_work_type(
        self, direction: Direction, predictor: Any
    ) -> None:
        with engine().connect() as conn:
            ctx = FeatureContext(conn=conn, facility_id=FACILITY_ID, at=AT)
            features = predictor.build_features(ctx)

        work_type = predictor.suggest_work_type(features, 0.5)
        assert work_type in direction.work_types

    def test_one_facility_computes_within_300_seconds(
        self, direction: Direction, predictor: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with engine().connect() as conn:
            ctx = FeatureContext(conn=conn, facility_id=FACILITY_ID, at=AT)
            features = predictor.build_features(ctx)

        stubbed = _stub_model(monkeypatch, predictor, list(features))

        started = time.monotonic()
        with engine().connect() as conn:
            ctx = FeatureContext(conn=conn, facility_id=FACILITY_ID, at=AT)
            predictor.build_features(ctx)
        try:
            predictor.predict(features)
        except RuntimeError:
            if not stubbed:
                pytest.skip(f"{direction.code}: модель не обучена, время не измерить")
            raise
        elapsed = time.monotonic() - started

        assert elapsed < 300.0
