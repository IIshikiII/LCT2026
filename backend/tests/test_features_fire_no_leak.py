"""Факты пожарного эпизода не смотрят в будущее. ADR 0016.

Сигналы, показания температуры и газа, обесточенная фаза после момента
расчёта не имеют права изменить ни один признак. Будущее здесь нарочно
«опасное»: пачка на объекте, тепловой сигнал рядом с дымом, скачок
температуры и метан выше предела воспламенения.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.features import fire
from app.synth.generate import generate
from app.tables import alarm_event, facility, sensor_reading
from tests.conftest import reset_database

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
AT = NOW - timedelta(hours=1)


@pytest.fixture
def db() -> Iterator[None]:
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")
    migrate.run()
    with engine().begin() as conn:
        reset_database(conn)
        generate(conn, seed=20260101, facility_count=60, now=NOW)
    yield
    with engine().begin() as conn:
        reset_database(conn)


def _vectors() -> dict[str, dict[str, float]]:
    with engine().connect() as conn:
        ids = conn.execute(select(facility.c.id).order_by(facility.c.id)).scalars().all()
        return {fid: fire.to_vector(fire.episode(conn, fid, AT)) for fid in ids}


def test_future_rows_change_no_feature(db: None) -> None:
    before = _vectors()
    assert any(v["signal"] for v in before.values()), "контроль: в окне есть сигналы"

    with engine().begin() as conn:
        rows = conn.execute(select(facility.c.id, facility.c.collector)).all()
        later = AT + timedelta(minutes=5)
        next_id = 10_000_000
        future = []
        for fid, _ in rows:
            for kind in (fire.SMOKE_DETECTED, fire.HEAT_OPEN, fire.PHASE_OFF):
                next_id += 1
                future.append(
                    {
                        "id": next_id,
                        "sensor_id": f"{fid}-FUTURE",
                        "facility_id": fid,
                        "occurred_at": later,
                        "alarm_type": kind,
                    }
                )
        conn.execute(insert(alarm_event), future)
        readings = []
        for fid, _ in rows:
            for metric, value in ((fire.METRIC_TEMPERATURE, 90.0), (fire.METRIC_METHANE, 9.0)):
                next_id += 1
                readings.append(
                    {
                        "id": next_id,
                        "sensor_id": f"{fid}-FUTURE",
                        "facility_id": fid,
                        "metric": metric,
                        "observed_at": later,
                        "value": value,
                    }
                )
        conn.execute(insert(sensor_reading), readings)

    after = _vectors()
    changed = {fid: name for fid, v in before.items() for name in v if after[fid][name] != v[name]}
    assert changed == {}, f"признаки заглянули в будущее: {changed}"
