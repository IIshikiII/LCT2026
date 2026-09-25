"""Экспертные правила пожарного риска. ADR 0016.

Правила чистые: факты на входе, уровень на выходе. Те же правила размечают
историю в `ml/fire/15_expert_history.py`, поэтому каждое правило уровня здесь
закреплено тестом.
"""

from __future__ import annotations

from app.ml.fire_rules import (
    NEXT_DAY_SIGNAL_RATE,
    OBJECT_BURN_IN_DAYS,
    SENSOR_BURN_IN_DAYS,
    Facts,
    assess,
)


def single(**values: object) -> Facts:
    return Facts(signal=True, smoke=True, **values)  # type: ignore[arg-type]


def test_no_signal_is_low() -> None:
    assert assess(Facts()).level == "LOW"


def test_a_burst_is_a_walk_and_raises_nothing() -> None:
    result = assess(Facts(burst_only=True))
    assert result.level == "LOW"
    assert result.tag == "обход"


def test_a_single_daytime_signal_is_low() -> None:
    assert assess(single()).level == "LOW"


def test_a_single_signal_at_night_without_a_walk_is_medium() -> None:
    assert assess(single(off_hours_no_walk=True)).level == "MEDIUM"


def test_spread_to_a_neighbour_is_high() -> None:
    assert assess(single(spread=True)).level == "HIGH"


def test_smoke_confirmed_by_heat_is_critical() -> None:
    assert assess(single(heat_independent=True)).level == "CRITICAL"


def test_spread_with_a_temperature_rise_is_critical() -> None:
    assert assess(single(spread=True, temp_rise_c=12.0)).level == "CRITICAL"


def test_methane_above_the_ignition_limit_is_critical_without_smoke() -> None:
    assert assess(Facts(gas_pct=6.0)).level == "CRITICAL"


def test_a_power_loss_is_a_note_and_not_a_sign() -> None:
    """Обесточенная фаза у 69 % эпизодов: это фон, а не признак пожара."""
    result = assess(single(power_off=True))
    assert result.level == "LOW"
    assert result.signs == 0
    assert any("справка" in reason for reason in result.reasons)


def test_methane_off_hours_lifts_a_single_signal_one_step() -> None:
    result = assess(single(off_hours_no_walk=True, gas_pct=1.5, gas_off_hours=True))
    assert result.level == "HIGH"


def test_two_signs_without_spread_or_heat_stay_medium() -> None:
    """Ночь и рост температуры без второго датчика это ещё не развитие пожара."""
    result = assess(single(off_hours_no_walk=True, temp_rise_c=11.0))
    assert result.signs == 2
    assert result.level == "MEDIUM"


def test_cooling_lowers_the_level_one_step() -> None:
    result = assess(single(spread=True, sensor_cooling=True, cooling_days_left=9))
    assert result.level == "MEDIUM"
    assert any("охлаждения" in reason for reason in result.reasons)


def test_cooling_never_lowers_critical() -> None:
    """Настоящий пожар на новом датчике тоже пожар."""
    result = assess(single(heat_independent=True, object_cooling=True, cooling_days_left=100))
    assert result.level == "CRITICAL"


def test_the_burn_in_periods_are_the_measured_ones() -> None:
    assert SENSOR_BURN_IN_DAYS == 15
    assert OBJECT_BURN_IN_DAYS == 180


def test_every_level_has_a_measured_next_day_rate() -> None:
    assert set(NEXT_DAY_SIGNAL_RATE) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert all(0 < rate < 1 for rate in NEXT_DAY_SIGNAL_RATE.values())
