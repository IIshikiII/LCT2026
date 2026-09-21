"""Тесты защиты рабочей базы. ADR 0005.

Тесты снимают базу целиком через `TRUNCATE ... CASCADE`. Две вещи обязаны
работать всегда: адрес прогона указывает на базу с суффиксом `_test`, и чистка
отказывается идти на базе без такого суффикса.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.config import Config
from app.db import engine
from conftest import DEFAULT_DATABASE_URL, resolve_test_database_url
from tests.conftest import check_database

WORK_URL = "postgresql+psycopg://arm:arm@127.0.0.1:5432/arm"
TEST_URL = "postgresql+psycopg://arm:arm@127.0.0.1:5432/arm_test"


def test_engine_points_at_test_database() -> None:
    """Прогон идёт на тестовой базе, а не на рабочей."""
    assert (engine().url.database or "").endswith("_test")


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        # Ворота зовут pytest без переменных. Умолчание даёт тестовую базу.
        ({}, TEST_URL),
        # Рабочий адрес в окружении не переводит прогон на рабочую базу.
        ({"DATABASE_URL": WORK_URL}, TEST_URL),
        # Служба compose `dev` уже называет тестовую базу. Суффикс не удваивается.
        (
            {"DATABASE_URL": "postgresql+psycopg://arm:arm@db:5432/arm_test"},
            "postgresql+psycopg://arm:arm@db:5432/arm_test",
        ),
        # Явное имя переменной сильнее вывода из `DATABASE_URL`.
        (
            {"DATABASE_URL": WORK_URL, "TEST_DATABASE_URL": "postgresql+psycopg://a:b@db:5432/x"},
            "postgresql+psycopg://a:b@db:5432/x",
        ),
    ],
)
def test_resolve_test_database_url(
    monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], expected: str
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    assert make_url(resolve_test_database_url()) == make_url(expected)


def test_default_url_matches_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Умолчание корневого `conftest.py` совпадает с умолчанием `app/config.py`.

    Умолчания разойдутся значит вывод адреса даст не ту базу. Тест скажет об
    этом раньше, чем прогон снимет чужие данные.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert make_url(DEFAULT_DATABASE_URL) == make_url(Config.from_environ().database_url)


@pytest.mark.parametrize(
    ("host", "name"),
    [
        ("127.0.0.1", "arm"),  # рабочая база на локальном хосте
        ("db", "arm"),  # рабочая база в compose
        ("127.0.0.1", None),  # имени нет вовсе
        ("10.0.0.5", "arm_test"),  # тестовое имя на чужом хосте
    ],
)
def test_check_database_refuses(host: str, name: str | None) -> None:
    assert check_database(host, name) is not None


@pytest.mark.parametrize(
    ("host", "name"),
    [
        ("127.0.0.1", "arm_test"),
        ("localhost", "arm_test"),
        ("db", "arm_test"),
    ],
)
def test_check_database_allows(host: str, name: str) -> None:
    assert check_database(host, name) is None
