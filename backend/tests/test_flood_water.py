"""Разметка суток «вода или проверка» повторяет `ml/flood/11_pu_label.py`. ADR 0013.

Сценарий: вторник 3 марта 2026 года, рабочий день. Пикет A даёт три сигнала
днём, сосед B по тому же коллектору один. Накануне ночью у A была вода. Насос
A работал до сигнала и после него. Числа признаков считаются руками.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import insert, select, text

from app.db import engine
from app.features import flood, flood_water
from app.tables import alarm_event, collector, facility, flood_water_day, weather_hourly
from tests.conftest import reset_database

DAY = date(2026, 3, 3)


def msk(day: date, hour: int, minute: int = 0) -> datetime:
    """Московское время в UTC."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) - timedelta(hours=3)


EVENTS = [
    ("A", "A-P", flood.FLOODED, msk(DAY, 10)),
    ("A", "A-P", flood.FLOODED, msk(DAY, 10, 10)),
    ("A", "A-P", flood.FLOODED, msk(DAY, 11)),
    ("B", "B-P", flood.FLOODED, msk(DAY, 10, 30)),
    # Накануне ночью: правило времени зовёт это водой.
    ("A", "A-P", flood.FLOODED, msk(DAY - timedelta(days=1), 3)),
    # Работа насоса A: за двое суток до события час, в сутки события час.
    ("A", "A-P", flood.PUMP_ON, msk(DAY - timedelta(days=2), 12)),
    ("A", "A-P", flood.PUMP_OFF, msk(DAY - timedelta(days=2), 13)),
    ("A", "A-P", flood.PUMP_ON, msk(DAY, 9)),
    ("A", "A-P", flood.PUMP_OFF, msk(DAY, 9, 30)),
    ("A", "A-P", flood.PUMP_ON, msk(DAY, 10, 20)),
    ("A", "A-P", flood.PUMP_OFF, msk(DAY, 10, 50)),
]


@pytest.fixture
def db() -> Any:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 — база недоступна это пропуск, не падение
        pytest.skip(f"тестовая база недоступна: {error}")
    with engine().begin() as conn:
        reset_database(conn)
        conn.execute(
            insert(collector), [{"code": "K", "label": "К", "district": "SAO", "line": []}]
        )
        conn.execute(
            insert(facility),
            [
                {
                    "id": code,
                    "collector": "K",
                    "district": "SAO",
                    "address": code,
                    "lat": 0.0,
                    "lon": 0.0,
                    "facility_type": "chamber",
                    "is_active": True,
                }
                for code in ("A", "B")
            ],
        )
        conn.execute(
            insert(alarm_event),
            [
                {"id": i, "facility_id": f, "sensor_id": s, "alarm_type": k, "occurred_at": t}
                for i, (f, s, k, t) in enumerate(EVENTS, start=1)
            ],
        )
        start = msk(DAY - timedelta(days=3), 0)
        conn.execute(
            insert(weather_hourly),
            [
                {
                    "observed_at": start + timedelta(hours=i),
                    "district": flood.WEATHER_AREA,
                    "precip_mm": 1.0,
                    "snow_depth_m": 0.0,
                }
                for i in range(24 * 4)
            ],
        )
    yield
    with engine().begin() as conn:
        reset_database(conn)


def _rows() -> dict[tuple[str, date], dict[str, Any]]:
    with engine().connect() as conn:
        rows = flood_water.event_rows(conn, DAY - timedelta(days=1), DAY + timedelta(days=1))
    return {(row["facility_id"], row["day"]): row for row in rows}


@pytest.mark.usefixtures("db")
def test_the_event_features_repeat_the_training_script() -> None:
    rows = _rows()
    assert set(rows) == {("A", DAY - timedelta(days=1)), ("A", DAY), ("B", DAY)}
    a = rows[("A", DAY)]
    assert a["night"] is False
    assert a["n_hours"] == 2
    assert a["n_moments"] == 3
    assert a["has_pump"] == 1 and a["has_sensor"] == 0
    assert float(a["span_minutes"]) == 60
    assert a["n_episodes"] == 2
    assert a["neighbour_units"] == 1
    assert a["other_units_within_1h"] == 1
    assert a["signal_yesterday"] == 1
    # Обычный уровень: 60 минут и 2 смены в сутки до события.
    assert float(a["pump_on_ratio"]) == pytest.approx(1.0)
    assert float(a["pump_changes_ratio"]) == pytest.approx(2.0)
    # 30 минут до сигнала и 30 после при норме 60 / 12 = 5 минут.
    assert float(a["pump_on_before_2h_ratio"]) == pytest.approx(6.0)
    assert float(a["pump_on_after_2h_ratio"]) == pytest.approx(6.0)
    assert float(a["precip_3d_mm"]) == pytest.approx(72.0)
    assert rows[("A", DAY - timedelta(days=1))]["night"] is True


class _Booster:
    def __init__(self, score: float) -> None:
        self.score = score

    def predict(self, matrix: Any) -> list[float]:
        return [self.score] * len(matrix)


def _model(score: float) -> dict[str, Any]:
    return {
        "booster": _Booster(score),
        "features": list(flood_water.FEATURES),
        "c": 0.5,
        "cutoff": 0.4,
    }


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(("score", "water"), [(0.3, True), (0.1, False)])
def test_the_classifier_decides_the_daytime_signal(score: float, water: bool) -> None:
    rows = list(_rows().values())
    days = {
        (item.facility_id, item.day): item for item in flood_water.classify(rows, _model(score))
    }
    daytime = days[("A", DAY)]
    assert daytime.is_labelled is False
    assert daytime.p_water == pytest.approx(min(score / 0.5, 1.0))
    assert daytime.is_water is water
    night = days[("A", DAY - timedelta(days=1))]
    assert night.is_labelled and night.is_water and night.p_water == 1.0


@pytest.mark.usefixtures("db")
def test_without_the_classifier_only_the_time_rule_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flood_water, "classifier", lambda: None)
    with engine().begin() as conn:
        flood_water.refresh(conn, msk(DAY + timedelta(days=1), 8))
    with engine().connect() as conn:
        stored = {
            (row.facility_id, row.day): row.is_water
            for row in conn.execute(select(flood_water_day)).all()
        }
    assert stored == {
        ("A", DAY - timedelta(days=1)): True,
        ("A", DAY): False,
        ("B", DAY): False,
    }


@pytest.mark.usefixtures("db")
def test_the_current_day_is_never_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сутки точки расчёта не кончились: их разметка подсмотрела бы будущее."""
    monkeypatch.setattr(flood_water, "classifier", lambda: None)
    with engine().begin() as conn:
        flood_water.refresh(conn, msk(DAY, 23))
    with engine().connect() as conn:
        days = {row.day for row in conn.execute(select(flood_water_day)).all()}
    assert days == {DAY - timedelta(days=1)}
