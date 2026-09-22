"""Вход в систему целиком: пароль, второй фактор, регистрация ключа, аудит.

Тесты этого файла работают без подмены актёра: они и проверяют то, что обычно
подменяется. Фикстура `live_auth` снимает подмену из `conftest`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import totp
from app.db import engine
from app.main import API_PREFIX, app
from app.tables import action_log, app_user
from tests import roles

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)


@pytest.fixture(autouse=True)
def live_auth() -> Iterator[None]:
    """Снимает подмену актёра: здесь проверяется настоящая проверка токена."""
    roles.sign_out()
    yield


def post(path: str, **body: Any) -> Any:
    return client.post(f"{API_PREFIX}{path}", json=body)


def code_now() -> str:
    return totp.code_at(roles.TEST_TOTP_SECRET)


def sign_in(username: str = "ods") -> str:
    """Проходит оба шага и отдаёт токен сессии."""
    first = post("/auth/login", username=username, password=roles.TEST_PASSWORD)
    assert first.status_code == 200, first.text
    second = post("/auth/mfa", mfaToken=first.json()["mfaToken"], code=code_now())
    assert second.status_code == 200, second.text
    return str(second.json()["accessToken"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def account(username: str) -> Any:
    with engine().connect() as conn:
        return conn.execute(select(app_user).where(app_user.c.username == username)).one()


def audit_codes() -> list[str]:
    with engine().connect() as conn:
        return [
            row.action_code
            for row in conn.execute(select(action_log).where(action_log.c.entity_type == "auth"))
        ]


# --- первый шаг --------------------------------------------------------------


def test_a_correct_password_asks_for_the_code_and_gives_no_session() -> None:
    body = post("/auth/login", username="ods", password=roles.TEST_PASSWORD).json()

    assert body["status"] == "MFA_REQUIRED"
    assert body["mfaToken"]
    assert "accessToken" not in body
    assert body["secret"] is None


def test_a_wrong_password_answers_401() -> None:
    assert post("/auth/login", username="ods", password="не тот").status_code == 401


def test_an_unknown_login_answers_the_same_as_a_wrong_password() -> None:
    """Разные ответы дали бы перебор имён сотрудников без единого пароля."""
    unknown = post("/auth/login", username="никого", password="любой")
    wrong = post("/auth/login", username="ods", password="не тот")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


# --- второй шаг --------------------------------------------------------------


def test_the_right_code_opens_the_session() -> None:
    first = post("/auth/login", username="ods", password=roles.TEST_PASSWORD).json()
    body = post("/auth/mfa", mfaToken=first["mfaToken"], code=code_now()).json()

    assert body["tokenType"] == "Bearer"
    assert body["expiresIn"] > 0
    assert body["user"]["username"] == "ods"
    assert body["user"]["role"] == "ODS_DISPATCHER"
    assert body["user"]["roleLabel"] == "Диспетчер ОДС"


def test_a_wrong_code_does_not_open_the_session() -> None:
    first = post("/auth/login", username="ods", password=roles.TEST_PASSWORD).json()
    assert post("/auth/mfa", mfaToken=first["mfaToken"], code="000000").status_code == 401


def test_the_intermediate_token_does_not_open_the_api() -> None:
    """Пароль без кода не открывает ни одного эндпоинта данных."""
    first = post("/auth/login", username="ods", password=roles.TEST_PASSWORD).json()
    response = client.get(f"{API_PREFIX}/predictions", headers=auth(first["mfaToken"]))

    assert response.status_code == 401


def test_a_made_up_intermediate_token_answers_401() -> None:
    assert post("/auth/mfa", mfaToken="выдумка", code=code_now()).status_code == 401


# --- регистрация ключа -------------------------------------------------------


def test_a_fresh_account_gets_a_secret_and_a_link() -> None:
    body = post("/auth/login", username="fresh", password=roles.TEST_PASSWORD).json()

    assert body["status"] == "ENROLL_REQUIRED"
    assert body["secret"]
    assert body["otpauthUrl"].startswith("otpauth://totp/")


def test_the_secret_lands_in_the_account_only_after_the_code() -> None:
    """До подтверждения ключ не записан: иначе запись заперла бы человека."""
    body = post("/auth/login", username="fresh", password=roles.TEST_PASSWORD).json()
    assert account("fresh").totp_secret is None

    session = post("/auth/mfa", mfaToken=body["mfaToken"], code=totp.code_at(body["secret"]))
    assert session.status_code == 200, session.text

    row = account("fresh")
    assert row.totp_secret == body["secret"]
    assert row.mfa_enrolled is True


def test_a_wrong_code_leaves_the_account_without_a_key() -> None:
    body = post("/auth/login", username="fresh", password=roles.TEST_PASSWORD).json()
    assert post("/auth/mfa", mfaToken=body["mfaToken"], code="000000").status_code == 401

    assert account("fresh").mfa_enrolled is False


# --- сессия ------------------------------------------------------------------


def test_the_api_refuses_a_request_without_a_token() -> None:
    response = client.get(f"{API_PREFIX}/predictions")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_the_api_refuses_a_made_up_token() -> None:
    assert client.get(f"{API_PREFIX}/predictions", headers=auth("made.up.token")).status_code == 401


def test_a_session_token_opens_the_api() -> None:
    token = sign_in()
    assert client.get(f"{API_PREFIX}/predictions", headers=auth(token)).status_code == 200


def test_me_tells_who_signed_in() -> None:
    body = client.get(f"{API_PREFIX}/auth/me", headers=auth(sign_in("district"))).json()

    assert body["username"] == "district"
    assert body["scopeKind"] == "DISTRICT"
    assert body["scopeValue"] == "CAO"
    assert "decide" in body["permissions"]


def test_a_technician_has_no_permissions() -> None:
    body = client.get(f"{API_PREFIX}/auth/me", headers=auth(sign_in("tech"))).json()
    assert body["permissions"] == []


def test_login_marks_the_moment() -> None:
    assert account("ods").last_login_at is None
    sign_in()
    assert account("ods").last_login_at is not None


# --- открытые маршруты -------------------------------------------------------


def test_health_works_without_a_token() -> None:
    assert client.get("/healthz").status_code == 200


def test_the_api_description_opens_without_a_token() -> None:
    """Жюри и интегратор открывают Swagger до всякого входа."""
    assert client.get(f"{API_PREFIX}/openapi.json").status_code == 200


# --- журнал действий ---------------------------------------------------------


def test_the_audit_keeps_every_step_of_the_login() -> None:
    sign_in()
    assert audit_codes() == ["login"]


def test_the_audit_keeps_the_refusal() -> None:
    post("/auth/login", username="ods", password="не тот")
    assert audit_codes() == ["login_denied"]


def test_the_audit_keeps_the_enrollment() -> None:
    body = post("/auth/login", username="fresh", password=roles.TEST_PASSWORD).json()
    post("/auth/mfa", mfaToken=body["mfaToken"], code=totp.code_at(body["secret"]))

    assert audit_codes() == ["enroll_started", "enroll_done", "login"]


def test_the_audit_keeps_the_exit() -> None:
    token = sign_in()
    assert client.post(f"{API_PREFIX}/auth/logout", headers=auth(token)).status_code == 200
    assert audit_codes() == ["login", "logout"]


def test_the_action_log_names_the_person_from_the_token() -> None:
    """ТЗ §11: журнал связывает действие с человеком, а не с константой."""
    token = sign_in("district")
    response = client.post(
        f"{API_PREFIX}/predictions/P-1/actions/take", json={}, headers=auth(token)
    )
    assert response.status_code == 200, response.text

    with engine().connect() as conn:
        row = conn.execute(select(action_log).where(action_log.c.entity_type == "prediction")).one()
    assert row.actor == "district"
