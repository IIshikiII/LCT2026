"""Метрики и дашборд.

Проверяется контракт и то, чего в ответе быть не должно: направление без
оценки, законченный прогноз в верху списка риска, прогон без успеха.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.metrics import NEVER_MINUTES, TARGET_COMPUTE_MS, TARGET_HORIZON_HOURS
from app.db import engine
from app.main import API_PREFIX, app
from app.tables import model_metric, pipeline_run
from tests.conftest import NOW, RUNS

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)


def get(path: str, **params: Any) -> Any:
    response = client.get(f"{API_PREFIX}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- качество моделей ---


def test_model_metrics_return_a_bare_array() -> None:
    body = get("/metrics/models")
    assert isinstance(body, list)
    assert set(body[0]) == {
        "direction",
        "precision",
        "recall",
        "targetPrecision",
        "targetRecall",
        "evaluatedAt",
        "method",
        "note",
        "baselineRule",
        "baselinePrecision",
        "baselineRecall",
    }


def test_every_entry_carries_the_targets_of_the_terms_of_reference() -> None:
    """Фронт цели не хардкодит, поэтому сервер шлёт их в каждой записи."""
    for item in get("/metrics/models"):
        assert item["targetPrecision"] == 0.7
        assert item["targetRecall"] == 0.5


def test_only_the_newest_evaluation_of_a_direction_is_returned() -> None:
    rows = {item["direction"]: item for item in get("/metrics/models")}
    assert rows["SENSOR_FAILURE"]["precision"] == 0.81


def test_a_direction_without_an_evaluation_is_absent() -> None:
    """Ноль читался бы как «модель не работает». Верное чтение — «оценки нет»."""
    codes = [item["direction"] for item in get("/metrics/models")]
    assert "UNAUTHORIZED_ACCESS" not in codes


def test_the_order_follows_the_direction_registry() -> None:
    codes = [item["direction"] for item in get("/metrics/models")]
    assert codes == ["SENSOR_FAILURE", "FIRE_RISK"]


def test_metrics_survive_an_empty_table() -> None:
    """Без замеров остаются только направления, которые сами сказали, что
    точность не измерена. Чисел у них нет, пояснение есть. ADR 0016."""
    with engine().begin() as conn:
        conn.execute(delete(model_metric))
    body = get("/metrics/models")
    assert [item["direction"] for item in body] == ["FIRE_RISK"]
    assert body[0]["precision"] is None
    assert body[0]["method"] == "expert_rules"
    assert "не измерена" in body[0]["note"]


# --- здоровье конвейера ---


def test_pipeline_health_counts_the_last_successful_run() -> None:
    body = get("/metrics/pipeline")
    started = NOW + timedelta(hours=RUNS[0][1], milliseconds=RUNS[0][2])
    assert body["lastRunAt"] == started.isoformat(timespec="seconds").replace("+00:00", "Z")
    assert body["lastRunMs"] == 42_000


def test_pipeline_health_measures_the_predictions_of_that_run() -> None:
    body = get("/metrics/pipeline")
    assert body["maxComputeMs"] == 1200
    assert body["minHorizonHours"] == 48


def test_pipeline_health_carries_both_targets() -> None:
    body = get("/metrics/pipeline")
    assert body["targetComputeMs"] == TARGET_COMPUTE_MS == 300_000
    assert body["targetHorizonHours"] == TARGET_HORIZON_HOURS == 24


def test_stream_lag_comes_from_the_last_run_with_events() -> None:
    """Тихая минута без событий не стирает задержку: виджет не мигает."""
    with engine().begin() as conn:
        for shift, events, lag in ((-0.6, 3, 45_000), (-0.5, 0, None)):
            conn.execute(
                pipeline_run.insert(),
                {
                    "started_at": NOW + timedelta(hours=shift),
                    "finished_at": NOW + timedelta(hours=shift, seconds=10),
                    "duration_ms": 10_000,
                    "prediction_count": 0,
                    "status": "DONE",
                    "stream_events": events,
                    "stream_lag_ms": lag,
                },
            )

    body = get("/metrics/pipeline")
    assert body["streamLagMs"] == 45_000
    assert body["streamEvents"] == 3


def test_freshness_grows_from_the_moment_of_the_run() -> None:
    body = get("/metrics/pipeline")
    expected = (datetime.now(UTC) - (NOW + timedelta(hours=RUNS[0][1]))).total_seconds() / 60
    assert body["freshnessMinutes"] == pytest.approx(expected, abs=2.0)


def test_a_run_in_progress_does_not_count_as_the_last_one() -> None:
    """Иначе свежесть показывала бы успех, которого ещё не было."""
    assert get("/metrics/pipeline")["lastRunMs"] == 42_000


def test_without_any_run_the_answer_fails_both_targets() -> None:
    with engine().begin() as conn:
        conn.execute(delete(pipeline_run))

    body = get("/metrics/pipeline")
    assert body["lastRunAt"] == ""
    assert body["freshnessMinutes"] == NEVER_MINUTES
    assert body["minHorizonHours"] < body["targetHorizonHours"]


# --- дашборд ---


def test_summary_returns_four_dictionaries_and_the_total() -> None:
    body = get("/dashboard/summary")
    assert set(body) == {"byLevel", "byDirection", "byStatus", "byOrderStatus", "total"}
    assert body["total"] == 5


def test_summary_counts_predictions_by_level() -> None:
    assert get("/dashboard/summary")["byLevel"] == {
        "LOW": 1,
        "MEDIUM": 1,
        "HIGH": 1,
        "CRITICAL": 2,
    }


def test_summary_counts_orders_separately_from_predictions() -> None:
    body = get("/dashboard/summary")
    assert body["byOrderStatus"] == {
        "AUTO_CREATED": 1,
        "CONFIRMED": 1,
        "IN_PROGRESS": 1,
        "REJECTED": 0,
        "CLOSED_CONFIRMED": 1,
        "CLOSED_NOT_CONFIRMED": 0,
    }
    assert sum(body["byStatus"].values()) == body["total"]


def test_a_direction_without_predictions_still_gets_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Иначе направление пропало бы с дашборда до первого прогноза.

    COLD_RISK выключен по умолчанию и прогнозов в наборе не имеет. Включаем
    его только здесь, чтобы проверить именно пустое направление.
    """
    import app.api.dashboard as dash
    from app.meta.directions import REGISTRY

    monkeypatch.setattr(dash, "active", lambda: REGISTRY)
    assert get("/dashboard/summary")["byDirection"]["COLD_RISK"] == 0


# --- верх списка риска ---


def test_top_risks_sort_by_probability_descending() -> None:
    values = [item["probability"] for item in get("/dashboard/top-risks")]
    assert values == sorted(values, reverse=True)


def test_top_risks_drop_the_finished_predictions() -> None:
    """Признак конца работы даёт `terminal` в реестре статусов."""
    codes = [item["id"] for item in get("/dashboard/top-risks")]
    assert codes == ["P-1", "P-3", "P-2"]


def test_top_risks_take_the_limit() -> None:
    assert len(get("/dashboard/top-risks", limit=1)) == 1


def test_a_broken_limit_does_not_break_the_widget() -> None:
    assert len(get("/dashboard/top-risks", limit=0)) == 1
    assert len(get("/dashboard/top-risks", limit=-5)) == 1
