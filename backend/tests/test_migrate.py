"""Проверка запускалки миграций. Нужна работающая тестовая база."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import migrate
from app.db import engine


@pytest.fixture
def clean_database() -> None:
    """Отдаёт пустую тестовую базу. Схема public пересоздаётся целиком."""
    try:
        with engine().begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as error:
        pytest.skip(f"тестовая база недоступна: {error}")


def test_files_sorted_by_version() -> None:
    versions = [version for version, _ in migrate.files()]
    assert versions == sorted(versions)
    assert "001" in versions


@pytest.mark.usefixtures("clean_database")
def test_migrate_runs_once_and_is_repeatable(caplog: pytest.LogCaptureFixture) -> None:
    # Уровень INFO включён намеренно. Он ловит конфликт имён в extra.
    caplog.set_level(logging.INFO)
    first = migrate.run()
    assert "001" in first

    second = migrate.run()
    assert second == []

    with engine().connect() as conn:
        applied = migrate.applied(conn)
        tables = (
            conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            .scalars()
            .all()
        )

    assert applied == set(first)
    assert {"facility", "prediction", "work_order", "schema_migration"} <= set(tables)


@pytest.mark.usefixtures("clean_database")
def test_alarm_event_is_partitioned() -> None:
    migrate.run()
    with engine().connect() as conn:
        kind = conn.execute(
            text("SELECT relkind FROM pg_class WHERE relname = 'alarm_event'")
        ).scalar_one()
    assert kind == "p"
