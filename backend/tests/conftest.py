"""Фикстура данных для тестов эндпоинтов.

Это не имитация реальных выгрузок. Реальных данных нет, и угадать их нельзя.
Фикстура закрывает ветви кода: каждый уровень риска, каждый статус обеих
сущностей, объект с двумя прогнозами, направление без прогнозов и направление
с битыми блоками.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.tables import action_log, collector, facility, prediction, work_order

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

FACILITIES = [
    # id, коллектор, район, адрес, lon, lat
    ("F-1", "K-CAO-1", "CAO", "Тверская улица, 1", 37.61, 55.75),
    ("F-2", "K-CAO-1", "CAO", "Арбат, 12", 37.59, 55.75),
    ("F-3", "K-SAO-1", "SAO", "Ленинградское шоссе, 40", 37.50, 55.85),
]

# probability подобрана под пороги уровней: 0.3, 0.55, 0.78.
PREDICTIONS = [
    # id, направление, объект, вероятность, уровень, статус, сдвиг в часах
    ("P-1", "SENSOR_FAILURE", "F-1", 0.91, "CRITICAL", "NEW", 0),
    ("P-2", "SENSOR_FAILURE", "F-1", 0.40, "MEDIUM", "IN_REVIEW", -1),
    ("P-3", "FIRE_RISK", "F-2", 0.60, "HIGH", "ORDER_CONFIRMED", -30),
    ("P-4", "UNAUTHORIZED_ACCESS", "F-3", 0.10, "LOW", "REJECTED", -60),
    ("P-5", "FIRE_RISK", "F-3", 0.80, "CRITICAL", "CLOSED", -80),
]

GOOD_BLOCKS = [
    {"type": "factors", "title": "Факторы", "data": {"items": [{"label": "Шум", "weight": 0.6}]}},
    {"type": "timeseries", "title": "Динамика", "data": {"series": []}},
]

# Направление UNAUTHORIZED_ACCESS отдаёт мусор намеренно. Спецификация §13.
BROKEN_BLOCKS = [
    {"type": "factors"},
    None,
    "строка вместо блока",
    {"type": "experimental_heatmap", "title": "Неизвестный тип", "data": {"cells": []}},
]

ORDERS = [
    # id, номер, прогноз, объект, статус, сдвиг срока в часах, итог
    ("O-1", "2026-0001", "P-1", "F-1", "AUTO_CREATED", 12, None),
    ("O-2", "2026-0002", "P-3", "F-2", "CONFIRMED", 24, None),
    ("O-3", "2026-0003", "P-4", "F-3", "IN_PROGRESS", 48, None),
    (
        "O-4",
        "2026-0004",
        "P-5",
        "F-3",
        "DONE",
        -6,
        {
            "actualCause": "HOT_WORKS",
            "predictionConfirmed": True,
            "comment": "подтвердилось при осмотре",
            "closedAt": "2026-09-09T10:00:00Z",
        },
    ),
]


def _insert(conn: object) -> None:
    conn.execute(  # type: ignore[attr-defined]
        collector.insert(),
        [
            {
                "code": "K-CAO-1",
                "label": "Коллектор Центральный 1",
                "district": "CAO",
                "line": [[37.58, 55.74], [37.61, 55.75]],
            }
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        facility.insert(),
        [
            {
                "id": code,
                "collector": collector_code,
                "section": "У-1",
                "chamber": "К-2",
                "device": None,
                "district": district,
                "address": address,
                "lat": lat,
                "lon": lon,
                "facility_type": "chamber",
                "is_active": True,
            }
            for code, collector_code, district, address, lon, lat in FACILITIES
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        prediction.insert(),
        [
            {
                "id": code,
                "direction": direction,
                "facility_id": facility_id,
                "probability": probability,
                "level": level,
                "horizon_hours": 48,
                "computed_at": NOW + timedelta(hours=shift),
                "computed_at_bucket": NOW + timedelta(hours=shift),
                "compute_ms": 1200,
                "status": status,
                "summary": f"прогноз {code}",
                "blocks": (BROKEN_BLOCKS if direction == "UNAUTHORIZED_ACCESS" else GOOD_BLOCKS),
                "model_version": "test",
            }
            for code, direction, facility_id, probability, level, status, shift in PREDICTIONS
        ],
    )
    conn.execute(  # type: ignore[attr-defined]
        work_order.insert(),
        [
            {
                "id": code,
                "number": number,
                "prediction_id": prediction_id,
                "facility_id": facility_id,
                "work_type": "Диагностика датчика",
                "due_at": NOW + timedelta(hours=due_shift),
                "status": status,
                "created_at": NOW - timedelta(hours=2),
                "outcome": outcome,
            }
            for code, number, prediction_id, facility_id, status, due_shift, outcome in ORDERS
        ],
    )


@pytest.fixture
def seeded() -> Iterator[None]:
    """Наполняет тестовую базу и чистит её после теста."""
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as conn:
        for table in (action_log, work_order, prediction, facility, collector):
            conn.execute(delete(table))
        _insert(conn)

    yield

    with engine().begin() as conn:
        for table in (action_log, work_order, prediction, facility, collector):
            conn.execute(delete(table))
