"""Запускалка миграций. Применяет .sql файлы из migrations/ по возрастанию номера."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db import engine

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
NAME_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     text PRIMARY KEY,
    filename    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def files(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """Отдаёт пары (версия, файл), отсортированные по версии.

    Файл с именем не по образцу NNN_имя.sql — это ошибка, а не повод его пропустить.
    """
    found: list[tuple[str, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = NAME_PATTERN.match(path.name)
        if match is None:
            raise ValueError(f"имя миграции не по образцу NNN_имя.sql: {path.name}")
        found.append((match.group(1), path))
    versions = [version for version, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"две миграции с одним номером: {versions}")
    return found


def applied(conn: Connection) -> set[str]:
    rows = conn.execute(text("SELECT version FROM schema_migration")).scalars().all()
    return set(rows)


def run(directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Применяет неприменённые миграции. Одна транзакция на файл."""
    with engine().begin() as conn:
        conn.execute(text(_CREATE_TABLE))

    done: list[str] = []
    with engine().connect() as conn:
        known = applied(conn)

    for version, path in files(directory):
        if version in known:
            continue
        sql = path.read_text(encoding="utf-8")
        with engine().begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text(
                    "INSERT INTO schema_migration (version, filename) VALUES (:version, :filename)"
                ),
                {"version": version, "filename": path.name},
            )
        # Имена в extra не должны совпадать со служебными полями LogRecord.
        # Поэтому здесь migration_file, а не filename.
        log.info("миграция применена", extra={"version": version, "migration_file": path.name})
        done.append(version)

    if not done:
        log.info("новых миграций нет")
    return done
