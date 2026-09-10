"""Автоматические заявки. Пороги и срок берутся у направления, не из кода."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.domain import auto_orders
from app.meta import by_code
from app.tables import facility, prediction, work_order

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[Connection]:
    """Пустая база и одно соединение в транзакции на тест."""
    try:
        with engine().connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")

    migrate.run()
    with engine().begin() as connection:
        for table in (work_order, prediction, facility):
            connection.execute(delete(table))
        connection.execute(
            facility.insert(),
            [
                {
                    "id": "F-1",
                    "collector": "K-1",
                    "district": "CAO",
                    "address": "Тверская, 1",
                    "lat": 55.75,
                    "lon": 37.61,
                    "facility_type": "chamber",
                    "is_active": True,
                }
            ],
        )
        yield connection
        for table in (work_order, prediction, facility):
            connection.execute(delete(table))


def add_prediction(
    conn: Connection,
    code: str,
    level: str,
    direction: str = "SENSOR_FAILURE",
    horizon: int = 48,
    at: datetime = NOW,
) -> auto_orders.Candidate:
    """Кладёт прогноз в базу.

    Прогнозы на один объект и направление обязаны лежать в разных окнах:
    уникальный индекс закрывает идемпотентность прогона.
    """
    conn.execute(
        prediction.insert().values(
            id=code,
            direction=direction,
            facility_id="F-1",
            probability=0.9,
            level=level,
            horizon_hours=horizon,
            computed_at=at,
            computed_at_bucket=at,
            compute_ms=100,
            status="NEW",
            summary=code,
        )
    )
    return auto_orders.Candidate(code, direction, "F-1", level, horizon, at)


def reject(conn: Connection, code: str, until: datetime) -> None:
    """Имитирует отклонение: статус и момент снятия мьюта."""
    conn.execute(
        update(prediction)
        .where(prediction.c.id == code)
        .values(status="REJECTED", suppress_until=until)
    )
    conn.execute(update(work_order).values(status="REJECTED"))


def orders(conn: Connection) -> list[object]:
    return list(conn.execute(select(work_order)).all())


@pytest.mark.parametrize("level", ["HIGH", "CRITICAL"])
def test_a_dangerous_level_creates_an_order(conn: Connection, level: str) -> None:
    assert auto_orders.create_for(conn, add_prediction(conn, "P-1", level)) is not None
    assert len(orders(conn)) == 1


@pytest.mark.parametrize("level", ["LOW", "MEDIUM"])
def test_a_calm_level_creates_nothing(conn: Connection, level: str) -> None:
    assert auto_orders.create_for(conn, add_prediction(conn, "P-1", level)) is None
    assert orders(conn) == []


def test_the_due_date_is_half_of_the_horizon(conn: Connection) -> None:
    # Срок в конце горизонта привёл бы бригаду ровно к предполагаемой аварии.
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH", horizon=48))
    row = orders(conn)[0]
    assert row.due_at == NOW + timedelta(hours=24)


def test_the_coefficient_comes_from_the_direction(conn: Connection) -> None:
    assert by_code("SENSOR_FAILURE").due_factor == 0.5
    assert by_code("SENSOR_FAILURE").order_levels == ("HIGH", "CRITICAL")


def test_the_work_type_comes_from_the_direction(conn: Connection) -> None:
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH", direction="FIRE_RISK"))
    assert orders(conn)[0].work_type in by_code("FIRE_RISK").work_types


def test_an_open_order_blocks_a_second_one(conn: Connection) -> None:
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    later = add_prediction(conn, "P-2", "CRITICAL", at=NOW + timedelta(minutes=15))
    assert auto_orders.create_for(conn, later) is None
    assert len(orders(conn)) == 1


def test_another_direction_gets_its_own_order(conn: Connection) -> None:
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    auto_orders.create_for(conn, add_prediction(conn, "P-2", "HIGH", direction="FIRE_RISK"))
    assert len(orders(conn)) == 2


def test_a_finished_order_does_not_block_a_new_one(conn: Connection) -> None:
    # Выполненная заявка не запрещает новую: состояние объекта могло ухудшиться.
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    conn.execute(update(work_order).values(status="DONE"))
    later = add_prediction(conn, "P-2", "HIGH", at=NOW + timedelta(minutes=15))
    assert auto_orders.create_for(conn, later) is not None
    assert len(orders(conn)) == 2


def test_a_rejection_blocks_the_same_level(conn: Connection) -> None:
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    reject(conn, "P-1", NOW + timedelta(days=7))

    later = add_prediction(conn, "P-2", "HIGH", at=NOW + timedelta(minutes=15))
    assert auto_orders.create_for(conn, later) is None, "отклонённый уровень повторять нельзя"


def test_a_higher_level_breaks_through_the_rejection(conn: Connection) -> None:
    # Отказ на HIGH ничего не говорит о CRITICAL.
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    reject(conn, "P-1", NOW + timedelta(days=7))

    later = add_prediction(conn, "P-2", "CRITICAL", at=NOW + timedelta(minutes=15))
    assert auto_orders.create_for(conn, later) is not None


def test_the_mute_expires(conn: Connection) -> None:
    # Мьют — суждение о текущем состоянии, а не приговор объекту.
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    reject(conn, "P-1", NOW + timedelta(days=7))

    later = add_prediction(conn, "P-2", "HIGH", at=NOW + timedelta(days=8))
    assert auto_orders.create_for(conn, later) is not None


def test_the_mute_length_follows_the_reason() -> None:
    # Дубль живёт сутки, особенность объекта никуда не денется за месяц.
    day = auto_orders.suppress_until("DUPLICATE", "SENSOR_FAILURE", NOW)
    month = auto_orders.suppress_until("KNOWN_ISSUE", "SENSOR_FAILURE", NOW)
    default = auto_orders.suppress_until(None, "SENSOR_FAILURE", NOW)
    assert day == NOW + timedelta(hours=24)
    assert month == NOW + timedelta(hours=720)
    assert default == NOW + timedelta(hours=by_code("SENSOR_FAILURE").reject_cooldown_hours)


def test_an_unknown_direction_creates_nothing(conn: Connection) -> None:
    candidate = add_prediction(conn, "P-1", "HIGH", direction="НЕТ_ТАКОГО")
    assert auto_orders.create_for(conn, candidate) is None


def test_the_number_looks_human_readable(conn: Connection) -> None:
    auto_orders.create_for(conn, add_prediction(conn, "P-1", "HIGH"))
    number = orders(conn)[0].number
    year, _, tail = number.partition("-")
    assert year.isdigit() and len(year) == 4
    assert tail.isdigit() and len(tail) >= 4


def test_numbers_do_not_repeat(conn: Connection) -> None:
    first = auto_orders.next_number(conn)
    second = auto_orders.next_number(conn)
    assert first != second


# --- прогон целиком ---


def test_a_run_creates_orders_for_its_own_bucket(conn: Connection) -> None:
    add_prediction(conn, "P-1", "HIGH")
    add_prediction(conn, "P-2", "LOW", direction="FIRE_RISK")
    created = auto_orders.create_missing(conn, NOW)
    assert len(created) == 1


def test_a_repeated_run_creates_nothing(conn: Connection) -> None:
    # Требование идемпотентности из §7: прогон идёт каждые 15 минут.
    add_prediction(conn, "P-1", "CRITICAL")
    assert len(auto_orders.create_missing(conn, NOW)) == 1
    assert auto_orders.create_missing(conn, NOW) == []
    assert len(orders(conn)) == 1
