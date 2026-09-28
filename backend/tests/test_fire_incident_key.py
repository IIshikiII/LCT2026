"""Карточка пожара на одно происшествие. ADR 0017, ADR 0018.

Ключ карточки это «объект, направление, начало происшествия». У пожара начало
это первый одиночный сигнал в окне 24 часов до момента расчёта. Тесты
проверяют, что одно происшествие даёт одну карточку, когда окно сдвигается,
сигнал опаздывает, сигнал уходит в пачку или газ пришёл раньше дыма.

Сейчас все четыре теста падают: начало происшествия каждый прогон считается
заново из скользящего окна, и любое изменение окна даёт новый ключ. Пометка
`xfail(strict=True)` держит дефект на виду. Исправление переведёт тесты в
зелёные, и тогда пометку надо снять. Задача стоит в `backend/TODO.md` §3.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.ml.plugins.fire_risk import FireRisk
from app.pipeline import run as pipeline_run_module
from app.tables import collector, facility, prediction, sensor
from tests.conftest import reset_database

pytestmark = pytest.mark.xfail(
    strict=True,
    reason="ключ карточки пожара считается из скользящего окна, backend/TODO.md §3",
)

T0 = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)  # среда, 12:00 по Москве
SECTION = "S-20-1"
# Пикет участка и три пикета соседнего участка того же объекта.
PICKETS = (
    ("P-20-10", SECTION, 100.0),
    ("P-20-60", "S-20-2", 600.0),
    ("P-20-70", "S-20-2", 700.0),
    ("P-20-80", "S-20-2", 800.0),
)


def _facility(fid: str, kind: str, parent: str | None, chainage: float | None) -> dict[str, object]:
    return {
        "id": fid,
        "collector": "20",
        "district": "комплекс",
        "address": fid,
        "lat": 55.75,
        "lon": 37.61,
        "facility_type": kind,
        "kind": kind,
        "gallery": -1,
        "chainage_m": chainage,
        "picket": chainage / 10 if chainage is not None else None,
        "parent_id": parent,
    }


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")
    migrate.run()
    with engine().begin() as conn:
        reset_database(conn)
        conn.execute(
            collector.insert().values(code="20", label="объект", district="комплекс", line=[])
        )
        conn.execute(
            facility.insert(),
            [
                _facility(SECTION, "section", None, None),
                _facility("S-20-2", "section", None, None),
                *(_facility(fid, "picket", parent, ch) for fid, parent, ch in PICKETS),
                _facility("G-1", "object", None, None),
            ],
        )
        conn.execute(
            sensor.insert(),
            [
                *(
                    {
                        "id": f"C-{fid}",
                        "facility_id": fid,
                        "sensor_type": "SMOKE",
                        "installed_at": datetime(2020, 1, 1).date(),
                        "channel_name": f"ДД ПК{int(ch / 10)}",
                    }
                    for fid, _, ch in PICKETS
                ),
                {
                    "id": "M-1",
                    "facility_id": "G-1",
                    "sensor_type": "METHANE",
                    "installed_at": datetime(2020, 1, 1).date(),
                    "channel_name": "Метан",
                },
            ],
        )
    plugin = FireRisk()
    monkeypatch.setattr(
        pipeline_run_module,
        "get_predictor",
        lambda code: plugin if code == "FIRE_RISK" else None,
    )
    yield
    with engine().begin() as conn:
        reset_database(conn)


def _smoke(fid: str, at: datetime) -> None:
    with engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO alarm_event (sensor_id, facility_id, occurred_at, alarm_type) "
                "VALUES (:s, :f, :t, 'SMOKE_DETECTED')"
            ),
            {"s": f"C-{fid}", "f": fid, "t": at},
        )


def _methane(at: datetime, pct: float) -> None:
    with engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sensor_reading (sensor_id, facility_id, observed_at, metric, value) "
                "VALUES ('M-1', 'G-1', :t, 'methane_pct', :v)"
            ),
            {"t": at, "v": pct},
        )


def _run(at: datetime) -> None:
    pipeline_run_module.run(engine(), at=at)


def _cards() -> list[tuple[datetime, str, float]]:
    """Карточки участка: начало происшествия, уровень, есть ли сигнал."""
    with engine().connect() as conn:
        rows = conn.execute(
            select(prediction.c.computed_at_bucket, prediction.c.level, prediction.c.features)
            .where(prediction.c.direction == "FIRE_RISK", prediction.c.facility_id == SECTION)
            .order_by(prediction.c.computed_at_bucket)
        ).all()
    return [(r[0], r[1], float((r[2] or {}).get("signal", 0.0))) for r in rows]


def _incident_cards() -> list[tuple[datetime, str, float]]:
    return [c for c in _cards() if c[2] > 0]


def test_a_long_episode_keeps_one_card_when_the_window_slides(db: None) -> None:
    """Дым повторяется 30 часов. Первый сигнал выходит из окна 24 часов."""
    for hours in (0, 12, 23, 30):
        _smoke("P-20-10", T0 + timedelta(hours=hours))
    for hours in (1, 13, 24.5, 31):
        _run(T0 + timedelta(hours=hours))
    assert len(_incident_cards()) == 1, _cards()


def test_a_late_signal_does_not_open_a_second_card(db: None) -> None:
    """Сигнал 08:00 доходит до базы после прогона 10:00."""
    _smoke("P-20-10", T0)
    _run(T0 + timedelta(hours=1))
    _smoke("P-20-10", T0 - timedelta(hours=1))
    _run(T0 + timedelta(hours=1, minutes=1))
    assert len(_incident_cards()) == 1, _cards()


def test_a_signal_that_joins_a_burst_leaves_no_orphan_card(db: None) -> None:
    """Одиночный сигнал становится пачкой, когда за час тревожат ещё три пикета.

    Пачка это обход, тревоги нет. Карточка одиночного сигнала не должна
    остаться висеть с прежним уровнем рядом с карточкой пачки.
    """
    _smoke("P-20-10", T0)
    _run(T0 + timedelta(minutes=5))
    for index, fid in enumerate(("P-20-60", "P-20-70", "P-20-80")):
        _smoke(fid, T0 + timedelta(minutes=10 + index))
    _run(T0 + timedelta(minutes=20))
    assert _incident_cards() == [], _cards()


def test_methane_before_smoke_gives_one_card(db: None) -> None:
    """Метан копится с утра, дым приходит днём. Это одно происшествие."""
    _methane(T0 - timedelta(hours=3), 1.5)
    _run(T0 - timedelta(hours=2))
    _methane(T0 - timedelta(hours=1), 3.0)
    _smoke("P-20-10", T0)
    _run(T0 + timedelta(minutes=5))
    assert len(_cards()) == 1, _cards()
