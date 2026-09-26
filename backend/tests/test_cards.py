"""Сборка карточки: демонстрационная шкала, живые блоки, сценарии. ADR 0018."""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime

import pytest

from app.ml.protocol import Block
from app.pipeline import cards
from app.stub import scenarios

ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@pytest.mark.parametrize("n", [12, 84, 420])
def test_the_demo_scale_keeps_the_levels_strictly_ordered(n: int) -> None:
    levels = cards.rank_levels({f"F-{i}": i / n for i in range(n)})
    counts = Counter(levels.values())
    assert 0 < counts["CRITICAL"] < counts["HIGH"] < counts["MEDIUM"] < counts["LOW"]


def test_the_demo_scale_gives_top_levels_to_the_most_likely() -> None:
    levels = cards.rank_levels({"A": 0.9, "B": 0.1, **{f"F-{i}": 0.01 for i in range(50)}})
    assert levels["A"] == "CRITICAL"
    assert levels["B"] in {"CRITICAL", "HIGH"}


def test_a_live_chart_replaces_the_static_one_and_keeps_factors() -> None:
    static = [
        {"type": "factors", "title": "Факторы", "data": {}},
        {"type": "timeseries", "title": "Старый график", "data": {"series": []}},
    ]
    live = [Block(type="timeseries", title="Живой график", data={"series": [1]})]

    merged = cards.merge(static, live)

    assert [b["title"] for b in merged] == ["Факторы", "Живой график"]


def test_the_freshness_block_names_both_moments() -> None:
    block = cards.freshness(
        datetime.fromisoformat("2026-09-25T21:00:00+00:00"),
        datetime.fromisoformat("2026-09-26T14:17:00+00:00"),
    )
    values = [item["value"] for item in block.data["items"]]
    assert values == ["на сутки от 00:00", "17:17"]


CANDIDATE = {
    "section": "S-1",
    "smoke": [1, 2],
    "pairs": [[1, 2]],
    "heat": [3],
    "temp": [4],
}


def test_the_scenarios_follow_their_signals() -> None:
    rng = random.Random(1)
    at = datetime(2026, 9, 16, 14, 0)
    critical = scenarios.rows_of("CRITICAL", CANDIDATE, at, rng)
    high = scenarios.rows_of("HIGH", CANDIDATE, at, rng)
    medium = scenarios.rows_of("MEDIUM", CANDIDATE, at, rng)

    assert {r.channel_id for r in critical} >= {1, 2, 3, 4}
    assert [r.value for r in high] == ["Обнаружен дым", "Обнаружен дым"]
    assert len(medium) == 1


def test_a_medium_scenario_only_comes_at_night() -> None:
    rows = scenarios.plan(
        [CANDIDATE | {"section": f"S-{i}"} for i in range(200)],
        datetime(2026, 9, 16, 0, 0),
        datetime(2026, 9, 17, 0, 0),
        random.Random(7),
        scenarios.never_day_off,
    )
    medium = [r for r in rows if r.kind == "MEDIUM"]
    assert medium
    assert all(r.at.hour < 6 or r.at.hour >= 22 for r in medium)


def test_a_section_does_not_burn_twice_in_a_day() -> None:
    rows = scenarios.plan(
        [CANDIDATE],
        datetime(2026, 9, 16, 0, 0),
        datetime(2026, 9, 17, 0, 0),
        random.Random(3),
        scenarios.never_day_off,
    )
    starts = {r.at for r in rows if r.value == "Обнаружен дым"}
    assert len({r.kind for r in rows}) <= 1
    assert len(starts) <= 2


@pytest.mark.parametrize(
    ("probability", "text"),
    [
        (0.046, "Риск в 5,8 раза выше среднего по сети"),
        (0.016, "Риск в 2 раза выше среднего по сети"),
        (0.04, "Риск в 5 раз выше среднего по сети"),
        (0.1, "Риск в 12 раз выше среднего по сети"),
        (0.008, "Риск на уровне среднего по сети"),
        (0.004, "Риск ниже среднего по сети"),
    ],
)
def test_the_forecast_reads_as_times_the_network_average(probability: float, text: str) -> None:
    assert cards.relative_summary(probability, 0.008) == text
