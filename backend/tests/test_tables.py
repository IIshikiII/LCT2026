"""Определения таблиц обязаны совпадать со схемой, которую создаёт SQL."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine
from app.tables import metadata, prediction, work_order


@pytest.fixture
def migrated_database() -> None:
    """Отдаёт тестовую базу со всеми применёнными миграциями.

    Фикстура работает на каждый тест, а не на модуль. Соседний файл тестов
    пересоздаёт схему, поэтому общее состояние здесь было бы хрупким.
    """
    try:
        with engine().begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")
    migrate.run()


def _columns(name: str) -> dict[str, str]:
    with engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": name},
        ).all()
    return {row[0]: row[1] for row in rows}


@pytest.mark.usefixtures("migrated_database")
def test_every_table_exists_with_the_same_columns() -> None:
    missing: list[str] = []
    for table in metadata.sorted_tables:
        actual = _columns(table.name)
        if not actual:
            missing.append(f"{table.name}: таблицы нет в базе")
            continue
        declared = {column.name for column in table.columns}
        absent = declared - set(actual)
        extra = set(actual) - declared
        if absent:
            missing.append(f"{table.name}: нет колонок {sorted(absent)}")
        if extra:
            missing.append(f"{table.name}: не описаны колонки {sorted(extra)}")
    assert not missing, "\n".join(missing)


@pytest.mark.usefixtures("migrated_database")
def test_nullability_matches_for_key_columns() -> None:
    # Полное сравнение по всем таблицам дало бы шум на серверных умолчаниях.
    # Здесь проверяются колонки, от которых зависит поведение API.
    assert _columns(work_order.name)["prediction_id"] == "NO"
    assert _columns(prediction.name)["blocks"] == "NO"
    assert _columns(prediction.name)["model_version"] == "YES"


@pytest.mark.usefixtures("migrated_database")
def test_sensor_reading_is_partitioned() -> None:
    with engine().connect() as conn:
        kind = conn.execute(
            text("SELECT relkind FROM pg_class WHERE relname = 'sensor_reading'")
        ).scalar_one()
    assert kind == "p"
