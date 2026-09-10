"""Соединение с PostgreSQL. SQLAlchemy Core, без ORM."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import Connection

from app.config import config

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(config.database_url, pool_pre_ping=True, future=True)
    return _engine


def get_conn() -> Iterator[Connection]:
    """Зависимость чтения. Отдаёт соединение на время запроса."""
    with engine().connect() as conn:
        yield conn


def get_tx() -> Iterator[Connection]:
    """Зависимость записи. Коммитит на выходе, откатывает на ошибке.

    Действие диспетчера меняет две сущности сразу: прогноз и заявку. Без общей
    транзакции перевод мог бы выполниться наполовину.
    """
    with engine().begin() as conn:
        yield conn
