"""Панель тестового стенда: наборы учётных записей. ADR 0007.

Главное свойство, которое держит этот файл: **выключенный флаг не раздаёт
ничего**. Список приходит пустым, создание и удаление отвечают 404.

Конфигурация заморожена, поэтому тест подменяет не поле, а имя `config` в
модуле роутера. Перезагружать модули ради булева значения дороже и хрупче.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import test_stand as stand_module
from app.config import config
from app.db import engine
from app.main import API_PREFIX, app
from app.tables import app_user
from tests import roles

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)

PATH = f"{API_PREFIX}/auth/test-accounts"


@pytest.fixture
def stand_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Включает режим стенда на один тест."""
    monkeypatch.setattr(stand_module, "config", replace(config, test_stand=True))
    yield


@pytest.fixture(autouse=True)
def no_token() -> Iterator[None]:
    """Панель открыта без входа: она и рисуется на экране входа."""
    roles.sign_out()
    yield


def usernames_of(body: dict[str, Any]) -> list[str]:
    return [item["username"] for group in body["sets"] for item in group["accounts"]]


def stored(username: str) -> Any:
    with engine().connect() as conn:
        return conn.execute(select(app_user).where(app_user.c.username == username)).first()


# --- выключенный стенд -------------------------------------------------------


def test_a_disabled_stand_gives_nothing_away() -> None:
    body = client.get(PATH).json()

    assert body["enabled"] is False
    assert body["sets"] == []
    assert body["password"] == ""


def test_a_disabled_stand_refuses_to_create() -> None:
    assert client.post(PATH).status_code == 404


def test_a_disabled_stand_refuses_to_delete() -> None:
    assert client.delete(f"{PATH}/1").status_code == 404


# --- включённый стенд --------------------------------------------------------


def test_the_list_opens_without_a_token(stand_on: None) -> None:
    """Токена у человека на экране входа ещё нет, и панель обязана открыться."""
    response = client.get(PATH)

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_the_first_set_holds_every_role(stand_on: None) -> None:
    body = client.get(PATH).json()
    first = body["sets"][0]

    assert first["set"] == 1
    assert {item["role"] for item in first["accounts"]} == {
        "ODS_DISPATCHER",
        "DISTRICT_DISPATCHER",
        "TECHNICIAN",
        "RESPONSE_TEAM",
    }


def test_the_panel_names_the_password(stand_on: None) -> None:
    assert client.get(PATH).json()["password"] == config.seed_password


def test_the_panel_tells_whether_the_key_is_set(stand_on: None) -> None:
    """Проверяющий не должен брать набор, занятый чужим телефоном."""
    accounts = client.get(PATH).json()["sets"][0]["accounts"]

    # Фикстура заводит ключ всем, кроме записи `fresh`, которой в наборах нет.
    assert all(item["mfaEnrolled"] for item in accounts)


def test_creating_a_set_adds_four_accounts_with_a_suffix(stand_on: None) -> None:
    body = client.post(PATH).json()

    assert [group["set"] for group in body["sets"]] == [1, 2]
    assert usernames_of(body) == [
        "ods",
        "district",
        "tech",
        "crew",
        "ods-2",
        "district-2",
        "tech-2",
        "crew-2",
    ]


def test_a_new_set_starts_without_a_key(stand_on: None) -> None:
    """Новый набор берёт следующий проверяющий, и ключ заводит он сам."""
    client.post(PATH)
    assert stored("ods-2").mfa_enrolled is False


def test_a_new_set_keeps_the_scope_of_its_roles(stand_on: None) -> None:
    body = client.post(PATH).json()
    second = {item["username"]: item for item in body["sets"][1]["accounts"]}

    assert second["tech-2"]["scopeKind"] == "COMPLEX"
    assert second["tech-2"]["scopeValue"] == stored("tech").scope_value
    assert second["ods-2"]["scopeKind"] == "ALL"


def test_a_new_set_signs_in(stand_on: None) -> None:
    """Набор бесполезен, если им нельзя войти."""
    client.post(PATH)
    response = client.post(
        f"{API_PREFIX}/auth/login",
        json={"username": "ods-2", "password": config.seed_password},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ENROLL_REQUIRED"


def test_deleting_a_set_removes_it_whole(stand_on: None) -> None:
    client.post(PATH)
    body = client.delete(f"{PATH}/2").json()

    assert [group["set"] for group in body["sets"]] == [1]
    assert stored("ods-2") is None


def test_deleting_a_set_that_is_not_there_answers_404(stand_on: None) -> None:
    assert client.delete(f"{PATH}/99").status_code == 404


def test_deleting_a_set_does_not_touch_real_accounts(stand_on: None) -> None:
    """Условие идёт по номеру набора, а у настоящих записей он пустой."""
    assert stored("fresh") is not None
    client.delete(f"{PATH}/1")

    assert stored("fresh") is not None
    assert stored("ods") is None
