"""GET /meta строит весь интерфейс. Ошибка здесь ломает каждый экран."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.meta import catalog, directions

client = TestClient(app)


def get_meta() -> dict[str, object]:
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_meta_has_every_contract_key() -> None:
    body = get_meta()
    assert set(body) == {
        "directions",
        "riskLevels",
        "statuses",
        "districts",
        "journalColumns",
        "orderColumns",
        "dashboardWidgets",
        "reasons",
    }


def test_every_direction_declares_a_horizon_of_at_least_24_hours() -> None:
    # Требование ТЗ. Меньший горизонт делает прогноз бесполезным.
    for item in get_meta()["directions"]:  # type: ignore[union-attr]
        assert item["minHorizonHours"] >= 24


def test_every_status_carries_a_colour_and_marks_the_terminal_ones() -> None:
    statuses = get_meta()["statuses"]
    for item in statuses:  # type: ignore[union-attr]
        assert item["colorVar"].startswith("--state-")
    terminal = {item["code"] for item in statuses if item.get("terminal")}  # type: ignore[union-attr]
    assert terminal == {"REJECTED", "CLOSED", "DONE"}


def test_rejected_appears_once_per_scope() -> None:
    statuses = get_meta()["statuses"]
    scopes = [item["scope"] for item in statuses if item["code"] == "REJECTED"]  # type: ignore[union-attr]
    assert sorted(scopes) == ["order", "prediction"]


def test_every_direction_has_a_reason_list() -> None:
    body = get_meta()
    reasons = body["reasons"]
    assert catalog.REJECTION_REASONS_REF in reasons  # type: ignore[operator]
    for item in body["directions"]:  # type: ignore[union-attr]
        assert reasons[item["code"]]  # type: ignore[index]


def test_level_for_matches_the_mock_probability_bands() -> None:
    assert catalog.level_for(0.10) == "LOW"
    assert catalog.level_for(0.30) == "MEDIUM"
    assert catalog.level_for(0.54) == "MEDIUM"
    assert catalog.level_for(0.55) == "HIGH"
    assert catalog.level_for(0.78) == "CRITICAL"
    assert catalog.level_for(1.00) == "CRITICAL"


def test_level_for_uses_the_direction_bands() -> None:
    """Направление со своими границами считает уровень по ним. ADR 0004."""
    access = directions.UNAUTHORIZED_ACCESS
    assert catalog.level_for(0.05, access) == "LOW"
    assert catalog.level_for(0.051913, access) == "MEDIUM"
    assert catalog.level_for(0.16, access) == "MEDIUM"
    assert catalog.level_for(0.163753, access) == "HIGH"
    assert catalog.level_for(0.268961, access) == "CRITICAL"
    # Число из находки T31: общие пороги давали этому прогнозу уровень LOW.
    assert catalog.level_for(0.30, access) == "CRITICAL"


def test_level_for_falls_back_to_the_common_bands() -> None:
    """Направление без своих границ работает как раньше. ADR 0004 пункт 3."""
    for direction in (directions.SENSOR_FAILURE, directions.FIRE_RISK, directions.FLOOD_RISK):
        assert direction.level_thresholds == ()
        for probability in (0.10, 0.30, 0.54, 0.55, 0.78, 1.00):
            assert catalog.level_for(probability, direction) == catalog.level_for(probability)


def test_direction_bands_name_known_levels_in_order() -> None:
    """Граница обязана называть уровень из RISK_LEVELS и идти по возрастанию."""
    codes = {level.code for level in catalog.RISK_LEVELS}
    for direction in directions.REGISTRY:
        bounds = [value for _, value in direction.level_thresholds]
        assert all(code in codes for code, _ in direction.level_thresholds)
        assert bounds == sorted(bounds)


def test_the_access_band_of_high_equals_the_order_threshold() -> None:
    """Автозаявка создаётся там, где её назначил ADR 0002, на шкале калибратора.

    T37 положил `calibration.joblib` в `ARTIFACTS_DIR`, поэтому `HIGH` стоит на
    калиброванной шкале, правый столбец таблицы ADR 0004, а не на сырой шкале
    бустера ADR 0002.
    """
    access = directions.UNAUTHORIZED_ACCESS
    bands = dict(access.level_thresholds)
    assert access.order_levels == ("HIGH", "CRITICAL")
    assert bands["HIGH"] == 0.163753
