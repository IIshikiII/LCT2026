"""Ни один признак реестра не смотрит после момента расчёта.

Проверка идёт по всему реестру сразу, а не по списку имён. Поэтому признак,
добавленный завтра, попадает под неё автоматически: забыть его нельзя.

Утечка в этом проекте уже случалась. В суточной панели признак числа каналов
брал текущие сутки, PR-AUC подскочил до 0,955 при базе 0,56 %, и только
несуразность числа выдала ошибку.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.engine import Connection

from app.db import engine
from app.features import registry
from app.tables import alarm_event, collector, facility
from tests.conftest import reset_database

DIRECTION = "UNAUTHORIZED_ACCESS"
FACILITY = "F-LEAK"
NEIGHBOUR = "F-LEAK-2"
COLLECTOR = "K-LEAK"
AT = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)

PAST = [
    (FACILITY, "DOOR_OPEN", AT - timedelta(days=9, hours=3)),
    (FACILITY, "VOLUMETRIC", AT - timedelta(days=9, hours=2)),
    (FACILITY, "MOTION", AT - timedelta(days=7)),
    (FACILITY, "SECURITY_DISARMED", AT - timedelta(days=5, hours=8)),
    (FACILITY, "SECURITY_ARMED", AT - timedelta(days=5)),
    (FACILITY, "DOOR_OPEN", AT - timedelta(days=1, hours=4)),
    (NEIGHBOUR, "MOTION", AT - timedelta(days=2)),
]

# Всё, что стоит на моменте расчёта и позже. Признаки обязаны это не заметить.
FUTURE = [
    (FACILITY, "DOOR_OPEN", AT),
    (FACILITY, "MOTION", AT + timedelta(hours=1)),
    (FACILITY, "SECURITY_DISARMED", AT + timedelta(hours=2)),
    (NEIGHBOUR, "VOLUMETRIC", AT + timedelta(days=1)),
]


def _place(conn: Connection) -> None:
    conn.execute(
        insert(collector),
        [{"code": COLLECTOR, "label": "Коллектор для теста", "district": "CAO", "line": []}],
    )
    conn.execute(
        insert(facility),
        [
            {
                "id": code,
                "collector": COLLECTOR,
                "district": "CAO",
                "address": f"Улица, {code}",
                "lat": 55.75,
                "lon": 37.61,
                "facility_type": "chamber",
                "is_active": True,
            }
            for code in (FACILITY, NEIGHBOUR)
        ],
    )


def _events(conn: Connection, rows: list[tuple[str, str, datetime]], first_id: int) -> None:
    """Вставляет события. `alarm_event.id` задаётся явно: таблица секционирована
    по `occurred_at`, ключ составной, и последовательности за ним нет."""
    conn.execute(
        insert(alarm_event),
        [
            {
                "id": first_id + offset,
                "facility_id": code,
                "sensor_id": f"{code}-{kind}",
                "alarm_type": kind,
                "occurred_at": ts,
            }
            for offset, (code, kind, ts) in enumerate(rows)
        ],
    )


def _vector(names: list[str]) -> dict[str, float]:
    with engine().connect() as conn:
        return registry.build(DIRECTION, conn, FACILITY, AT, names)


@pytest.fixture
def names() -> list[str]:
    return sorted(registry.known(DIRECTION))


@pytest.fixture
def both(names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Вектор признаков без будущего и с будущим."""
    try:
        with engine().connect() as probe:
            probe.execute(delete(alarm_event).where(alarm_event.c.facility_id == "___"))
    except Exception as error:  # noqa: BLE001 — база недоступна это пропуск, не падение
        pytest.skip(f"тестовая база недоступна: {error}")

    with engine().begin() as conn:
        reset_database(conn)
        _place(conn)
        _events(conn, PAST, first_id=1)
    past_only = _vector(names)

    with engine().begin() as conn:
        _events(conn, FUTURE, first_id=1000)
    with_future = _vector(names)

    with engine().begin() as conn:
        reset_database(conn)
    return past_only, with_future


def test_the_registry_covers_both_models(names: list[str]) -> None:
    """Реестр обязан уметь считать оба обученных набора.

    Без этого замена файла модели перестаёт быть заменой файла.
    """
    hourly = [
        "n_alarms_1h",
        "hours_since_last_armed_alarm",
        "is_disarmed",
        "has_access_sequence",
    ]
    daily = [
        "days_since_last_armed",
        "armed_day_share_30d",
        "obj_units_alarmed_7d",
        "day_off_chain",
    ]
    assert registry.missing(DIRECTION, hourly + daily) == []
    assert len(names) >= 30


def test_an_unknown_feature_fails_loudly() -> None:
    """Модель просит неизвестный признак значит отказ называет его имя."""
    with pytest.raises(LookupError, match="выдуманный_признак"):
        registry.require(DIRECTION, ["n_alarms_1h", "выдуманный_признак"])


def test_the_point_sees_the_past(both: tuple[dict[str, float], dict[str, float]]) -> None:
    """Контроль: иначе тест на утечку прошёл бы и на нулевых признаках."""
    past_only, _ = both
    assert past_only["n_alarms_7d"] > 0
    assert past_only["disarm_hours_7d"] > 0
    assert past_only["n_channels"] > 0


def test_no_feature_reads_the_future(
    both: tuple[dict[str, float], dict[str, float]],
) -> None:
    past_only, with_future = both
    differs = [name for name, value in past_only.items() if with_future[name] != value]
    assert differs == [], f"эти признаки заглянули в будущее: {differs}"
