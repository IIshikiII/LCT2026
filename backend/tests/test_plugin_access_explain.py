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

# Участок без истории: `hours_since_*` равны возрасту плюс одни сутки.
QUIET: dict[str, float] = {
    "n_alarms_7d": 0.0,
    "n_armed_7d": 0.0,
    "armed_day_share_30d": 0.0,
    "hours_since_last_alarm": 24.0 * 41,
    "hours_since_last_armed": 24.0 * 41,
    "hours_since_guard_change": math.nan,
    "unit_age_days": 40.0,
    "obj_alarms_1d": 0.0,
    "obj_alarms_7d": 0.0,
    "is_day_off": 0.0,
    "day_of_month": 10.0,
}

BUSY: dict[str, float] = {
    **QUIET,
    "n_alarms_7d": 5.0,
    "n_armed_7d": 7.0,
    "armed_day_share_30d": 0.2,
    "hours_since_last_alarm": 2.0,
    "hours_since_last_armed": 6.0,
    "hours_since_guard_change": 30.0,
    "obj_alarms_1d": 3.0,
    "obj_alarms_7d": 15.0,
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
            ["n_alarms_7d", "armed_day_share_30d", "is_day_off"],
            [0.4, -1.6, 0.05],
            BUSY,
        )
        labels = [item["label"] for item in block.data["items"]]
        assert labels[0] == FEATURE_LABELS["armed_day_share_30d"]
        assert labels[1] == FEATURE_LABELS["n_alarms_7d"]
        assert labels[2] == FEATURE_LABELS["is_day_off"]

    def test_block_keeps_weights_inside_range(self) -> None:
        block = factors_block(["n_alarms_7d", "armed_day_share_30d"], [12.0, -9.0], BUSY)
        for item in block.data["items"]:
            assert -1.0 <= item["weight"] <= 1.0

    def test_block_shows_feature_value(self) -> None:
        block = factors_block(["n_alarms_7d"], [0.4], BUSY)
        assert block.data["items"][0]["value"] == "5"

    def test_empty_feature_reads_as_no_data(self) -> None:
        block = factors_block(["hours_since_guard_change"], [0.4], QUIET)
        assert block.data["items"][0]["value"] == "нет данных"

    def test_unknown_feature_keeps_its_own_name(self) -> None:
        block = factors_block(["pressure_drop_3h"], [0.4], {"pressure_drop_3h": 1.0})
        assert block.data["items"][0]["label"] == "pressure_drop_3h"

    def test_block_type_is_factors(self) -> None:
        assert factors_block(["n_alarms_7d"], [0.0], BUSY).type == "factors"

    def test_block_carries_a_note(self) -> None:
        block = factors_block(["n_alarms_7d"], [0.4], BUSY)
        assert block.data["note"]

    def test_weak_factor_is_dropped(self) -> None:
        names = ["n_alarms_7d", "armed_day_share_30d", "is_day_off", "day_of_month"]
        contributions = [4.0, 3.0, 1.0, 0.001]  # четвёртый вклад даёт вес ниже 0,02
        block = factors_block(names, contributions, BUSY)
        labels = [item["label"] for item in block.data["items"]]
        assert FEATURE_LABELS["day_of_month"] not in labels

    def test_three_strongest_items_survive_even_when_all_weak(self) -> None:
        names = ["n_alarms_7d", "armed_day_share_30d", "is_day_off", "day_of_month"]
        contributions = [0.001, 0.0009, 0.0008, 0.0007]
        block = factors_block(names, contributions, BUSY)
        assert len(block.data["items"]) == 3


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

    def test_windows_do_not_overlap(self) -> None:
        row = series_of(timeseries_block(BUSY, AT).data, "Тревоги на объекте")
        # Окно от 7 суток до 1 суток держит 15 - 3 = 12 тревог за 6 суток.
        assert [point["v"] for point in row["points"]] == [2.0, 3.0]

    def test_events_count_as_daily_rate(self) -> None:
        row = series_of(timeseries_block(BUSY, AT).data, "События на участке")
        assert row["points"] == [{"t": (AT - timedelta(days=3.5)).isoformat(), "v": 1.0}]

    def test_inconsistent_counts_never_give_negative_rate(self) -> None:
        broken = {**BUSY, "obj_alarms_7d": 1.0, "obj_alarms_1d": 40.0}
        row = series_of(timeseries_block(broken, AT).data, "Тревоги на объекте")
        assert all(point["v"] >= 0 for point in row["points"])

    def test_missing_feature_does_not_break_block(self) -> None:
        row = series_of(timeseries_block({}, AT).data, "Тревоги на объекте")
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

    def test_last_event_lands_on_its_hour(self) -> None:
        events = timeline_block(BUSY, AT).data["events"]
        event = next(item for item in events if item["title"] == "Последнее событие на участке")
        assert event["at"] == (AT - timedelta(hours=6)).isoformat()

    def test_unit_without_history_shows_only_the_forecast(self) -> None:
        events = timeline_block(QUIET, AT).data["events"]
        assert [event["kind"] for event in events] == ["forecast"]

    def test_guard_change_shows_its_moment(self) -> None:
        events = timeline_block(BUSY, AT).data["events"]
        change = next(item for item in events if item["title"] == "Смена режима охраны")
        assert change["at"] == (AT - timedelta(hours=30)).isoformat()

    def test_block_type_is_timeline(self) -> None:
        assert timeline_block(BUSY, AT).type == "timeline"
