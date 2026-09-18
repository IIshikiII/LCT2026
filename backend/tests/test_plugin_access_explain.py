"""Объяснение прогноза направления доступа.

Проверяются три правила из `docs/08-ml-plugin.md`: вес фактора, ряд наблюдений
и лента событий. Проверки идут по чистым функциям, без модели и без базы.
Причина: LightGBM и SHAP лежат в наборе `research`, а набор `ml` их пока не
держит. Вызов `explain` целиком проверяет `test_plugin_access.py` после
переноса зависимостей.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.ml.plugins.unauthorized_access import (
    FEATURE_LABELS,
    factors_block,
    shap_weight,
    timeline_block,
    timeseries_block,
)

AT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)

QUIET: dict[str, float] = {
    "n_alarms_1h": 0.0,
    "n_alarms_24h": 0.0,
    "n_alarms_168h": 0.0,
    "n_alarms_720h": 0.0,
    "alarm_hour_share_720h": 0.0,
    "hours_since_last_alarm": 0.0,
    "night_share": 0.0,
    "hour_of_day": 12.0,
    "day_of_week": 4.0,
    "month": 9.0,
    "is_weekend": 0.0,
    "neighbor_channels_1h": 0.0,
    "is_disarmed": 0.0,
    "has_access_sequence": 0.0,
    "n_armed_alarms_24h": 0.0,
    "n_armed_alarms_168h": 0.0,
    "hours_since_last_armed_alarm": 0.0,
}

BUSY: dict[str, float] = {
    **QUIET,
    "n_alarms_1h": 2.0,
    "n_alarms_24h": 5.0,
    "n_alarms_168h": 11.0,
    "n_alarms_720h": 34.0,
    "alarm_hour_share_720h": 0.03,
    "hours_since_last_alarm": 0.0,
    "night_share": 0.4,
    "neighbor_channels_1h": 3.0,
    "n_armed_alarms_24h": 1.0,
    "n_armed_alarms_168h": 7.0,
    "hours_since_last_armed_alarm": 6.0,
}


def series_of(block_data: dict, name: str) -> dict:
    return next(row for row in block_data["series"] if row["name"] == name)


class TestWeight:
    """Правило приведения вклада SHAP к диапазону от минус 1 до 1."""

    def test_zero_contribution_gives_zero_weight(self) -> None:
        assert shap_weight(0.0) == 0.0

    def test_weight_reads_as_odds_ratio(self) -> None:
        # Признак удваивает шанс значит вес равен (2 - 1) / (2 + 1).
        assert shap_weight(math.log(2)) == pytest.approx(1 / 3)
        assert shap_weight(math.log(3)) == pytest.approx(0.5)
        assert shap_weight(math.log(0.5)) == pytest.approx(-1 / 3)

    def test_weight_keeps_sign(self) -> None:
        assert shap_weight(0.7) > 0
        assert shap_weight(-0.7) < 0
        assert shap_weight(-0.7) == -shap_weight(0.7)

    def test_weight_keeps_order(self) -> None:
        # Наибольший вклад модели на отложенной выборке равен 4,0 логита.
        contributions = [-8.0, -4.0, -0.9, -0.01, 0.0, 0.01, 0.9, 4.0, 8.0]
        weights = [shap_weight(value) for value in contributions]
        assert weights == sorted(weights)
        assert len(set(weights)) == len(weights)

    def test_weight_never_leaves_range(self) -> None:
        for value in (-500.0, -40.0, 40.0, 500.0):
            assert -1.0 <= shap_weight(value) <= 1.0


class TestFactors:
    """Блок `factors`: порядок, подписи, диапазон."""

    def test_block_sorts_by_absolute_weight(self) -> None:
        block = factors_block(
            ["n_alarms_24h", "night_share", "is_weekend"],
            [0.4, -1.6, 0.05],
            BUSY,
        )
        labels = [item["label"] for item in block.data["items"]]
        assert labels[0] == FEATURE_LABELS["night_share"]
        assert labels[1] == FEATURE_LABELS["n_alarms_24h"]
        assert labels[2] == FEATURE_LABELS["is_weekend"]

    def test_block_keeps_weights_inside_range(self) -> None:
        block = factors_block(["n_alarms_24h", "night_share"], [12.0, -9.0], BUSY)
        for item in block.data["items"]:
            assert -1.0 <= item["weight"] <= 1.0

    def test_block_shows_feature_value(self) -> None:
        block = factors_block(["n_alarms_24h"], [0.4], BUSY)
        assert block.data["items"][0]["value"] == "5"

    def test_unknown_feature_keeps_its_own_name(self) -> None:
        block = factors_block(["pressure_drop_3h"], [0.4], {"pressure_drop_3h": 1.0})
        assert block.data["items"][0]["label"] == "pressure_drop_3h"

    def test_block_type_is_factors(self) -> None:
        assert factors_block(["n_alarms_1h"], [0.0], BUSY).type == "factors"


class TestTimeSeries:
    """Блок `timeseries`: средняя частота тревог по непересекающимся окнам."""

    def test_block_holds_two_series_and_marker(self) -> None:
        block = timeseries_block(BUSY, AT)
        assert block.type == "timeseries"
        assert len(block.data["series"]) == 2
        assert block.data["markerAt"] == AT.isoformat()

    def test_points_go_left_to_right(self) -> None:
        block = timeseries_block(BUSY, AT)
        for row in block.data["series"]:
            stamps = [point["t"] for point in row["points"]]
            assert stamps == sorted(stamps)

    def test_last_hour_counts_as_daily_rate(self) -> None:
        row = series_of(timeseries_block(BUSY, AT).data, "Тревоги доступа")
        # Две тревоги за последний час это 48 тревог в сутки.
        assert row["points"][-1]["v"] == 48.0
        assert row["points"][-1]["t"] == (AT - timedelta(minutes=30)).isoformat()

    def test_windows_do_not_overlap(self) -> None:
        row = series_of(timeseries_block(BUSY, AT).data, "Тревоги доступа")
        # Окно от 168 до 24 часов держит 11 - 5 = 6 тревог за 6 суток.
        assert row["points"][1]["v"] == 1.0

    def test_inconsistent_counts_never_give_negative_rate(self) -> None:
        broken = {**BUSY, "n_alarms_720h": 1.0, "n_alarms_168h": 40.0}
        row = series_of(timeseries_block(broken, AT).data, "Тревоги доступа")
        assert all(point["v"] >= 0 for point in row["points"])

    def test_missing_feature_does_not_break_block(self) -> None:
        row = series_of(timeseries_block({}, AT).data, "Тревоги доступа")
        assert all(point["v"] == 0 for point in row["points"])


class TestTimeline:
    """Блок `timeline`: события ленты и их порядок."""

    def test_freshest_event_comes_first(self) -> None:
        events = timeline_block(BUSY, AT).data["events"]
        stamps = [event["at"] for event in events]
        assert stamps == sorted(stamps, reverse=True)

    def test_block_always_shows_the_moment_of_forecast(self) -> None:
        for features in (QUIET, BUSY):
            events = timeline_block(features, AT).data["events"]
            assert any(event["at"] == AT.isoformat() for event in events)
            assert all(event["kind"] and event["title"] for event in events)

    def test_last_armed_alarm_lands_on_its_hour(self) -> None:
        events = timeline_block(BUSY, AT).data["events"]
        armed = next(event for event in events if "вне режима охраны" in event["title"])
        assert armed["at"] == (AT - timedelta(hours=6)).isoformat()

    def test_unit_without_history_gets_no_alarm_event(self) -> None:
        events = timeline_block(QUIET, AT).data["events"]
        assert not [event for event in events if event["kind"] == "alarm"]

    def test_disarmed_unit_shows_its_mode(self) -> None:
        events = timeline_block({**QUIET, "is_disarmed": 1.0}, AT).data["events"]
        assert any("снят с охраны" in event["title"] for event in events)

    def test_sequence_shows_the_chain(self) -> None:
        events = timeline_block({**BUSY, "has_access_sequence": 1.0}, AT).data["events"]
        assert any(event["kind"] == "access" for event in events)

    def test_block_type_is_timeline(self) -> None:
        assert timeline_block(BUSY, AT).type == "timeline"
