"""Вход в систему: пароль, второй фактор, регистрация ключа.

Вход разделён на два шага, и это не украшение. Первый шаг проверяет, что
человек знает пароль, и выдаёт промежуточный токен. Второй шаг проверяет, что
у него в руках телефон с ключом, и выдаёт токен сессии. Промежуточный токен не
открывает ни одного эндпоинта данных: его вид проверяется отдельно.

Третий исход первого шага — ключ ещё не заведён. Тогда сервер создаёт секрет и
отдаёт его один раз, вместе со ссылкой `otpauth://`. Человек заводит запись в
аутентификаторе и подтверждает её кодом. До подтверждения секрет в базу не
попадает: запись с заведённым, но непроверенным ключом заперла бы человека
снаружи.

**Что пишется в журнал.** Каждый шаг, удачный и нет, ложится в `action_log`
с `entity_type = 'auth'`. ТЗ §11 требует журналировать все действия
пользователей, и вход является первым из них. Пароль и код в журнал не идут.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Connection

from app.auth import directory, totp
from app.auth.actor import Actor, from_claims
from app.auth.directory import Account
from app.auth.tokens import KIND_ACCESS, KIND_MFA, TokenError, sign, verify
from app.config import config
from app.db import engine
from app.tables import action_log

AUTH = "auth"

# Исходы первого шага. Клиент выбирает по ним следующий экран.
NEED_CODE = "MFA_REQUIRED"
NEED_ENROLL = "ENROLL_REQUIRED"
GRANTED = "GRANTED"


class AuthError(Exception):
    """Вход не удался. Текст уходит пользователю, поэтому он общий."""


@dataclass(frozen=True)
class LoginResult:
    status: str
    # Промежуточный токен между паролем и кодом.
    mfa_token: str | None = None
    # Секрет и ссылка приходят один раз, только при регистрации ключа.
    secret: str | None = None
    otpauth_url: str | None = None


@dataclass(frozen=True)
class Session:
    token: str
    expires_in: int
    actor: Actor


def audit(
    conn: Connection, username: str, code: str, payload: dict[str, Any] | None = None
) -> None:
    """Пишет событие входа. Ни пароля, ни кода в теле нет."""
    conn.execute(
        action_log.insert().values(
            entity_type=AUTH,
            entity_id=username or "-",
            action_code=code,
            actor=username or "-",
            payload=payload or {},
        )
    )


def audit_denied(username: str, code: str, reason: str) -> None:
    """Пишет отказ своей транзакцией.

    Отказ поднимает исключение, а исключение откатывает транзакцию запроса
    вместе с записью в журнал. Без отдельной транзакции неудачный вход не
    попадал бы в журнал вовсе, а службе безопасности интересен именно он.
    """
    with engine().begin() as conn:
        audit(conn, username, code, {"reason": reason})


def _actor(account: Account) -> Actor:
    return Actor(
        username=account.username,
        full_name=account.full_name,
        role=account.role,
        scope_kind=account.scope_kind,
        scope_value=account.scope_value,
    )


def _mfa_token(account: Account, secret: str | None = None) -> str:
    """Промежуточный токен. Несёт имя и, при регистрации, новый секрет.

    Секрет едет в подписанном токене, а не в памяти процесса: сервер может
    работать в нескольких копиях, и второй шаг попадёт не в ту копию, что
    первый. Подпись не даёт клиенту подменить секрет на свой.
    """
    payload: dict[str, Any] = {"sub": account.username, "kind": KIND_MFA}
    if secret:
        payload["enroll"] = secret
    return sign(payload, config.auth_secret, config.auth_mfa_ttl_seconds)


def _session(conn: Connection, account: Account) -> Session:
    actor = _actor(account)
    ttl = config.auth_token_ttl_minutes * 60
    token = sign({**actor.claims(), "kind": KIND_ACCESS}, config.auth_secret, ttl)
    directory.mark_login(conn, account.username)
    audit(conn, account.username, "login", {"role": actor.role.code})
    return Session(token=token, expires_in=ttl, actor=actor)


def login(conn: Connection, username: str, password: str) -> LoginResult:
    """Первый шаг: логин и пароль."""
    account = directory.authenticate(conn, username, password)
    if account is None:
        audit_denied(username, "login_denied", "логин или пароль не подошли")
        raise AuthError("Логин или пароль не подошли")

    if not account.mfa_enrolled or not account.totp_secret:
        secret = totp.generate_secret()
        audit(conn, account.username, "enroll_started")
        return LoginResult(
            status=NEED_ENROLL,
            mfa_token=_mfa_token(account, secret),
            secret=secret,
            otpauth_url=totp.otpauth_url(secret, account.username),
        )

    return LoginResult(status=NEED_CODE, mfa_token=_mfa_token(account))


def _from_mfa_token(conn: Connection, token: str) -> tuple[Account, str | None]:
    try:
        payload = verify(token, config.auth_secret, KIND_MFA)
    except TokenError as error:
        raise AuthError("Время на ввод кода истекло, войдите заново") from error

    account = directory.find(conn, str(payload.get("sub", "")))
    if account is None or not account.is_active:
        raise AuthError("Учётная запись недоступна")
    enroll_secret = payload.get("enroll")
    return account, str(enroll_secret) if enroll_secret else None


def confirm(conn: Connection, mfa_token: str, code: str) -> Session:
    """Второй шаг: одноразовый код.

    Шаг один и для обычного входа, и для регистрации ключа. Разница только в
    том, откуда берётся секрет: у заведённого ключа из базы, у нового — из
    промежуточного токена. При удаче новый секрет записывается.
    """
    account, enroll_secret = _from_mfa_token(conn, mfa_token)
    secret = enroll_secret or account.totp_secret
    if not secret:
        raise AuthError("Ключ второго фактора не заведён")

    if not totp.verify(secret, code):
        audit_denied(account.username, "mfa_denied", "одноразовый код не подошёл")
        raise AuthError("Код не подошёл. Проверьте время на телефоне и повторите")

    if enroll_secret:
        directory.enroll(conn, account.username, enroll_secret)
        audit(conn, account.username, "enroll_done")

    return _session(conn, account)


def actor_from_token(token: str) -> Actor:
    """Разбирает токен сессии. Бросает `AuthError` на любой негодный токен."""
    try:
        return from_claims(verify(token, config.auth_secret, KIND_ACCESS))
    except (TokenError, ValueError) as error:
        raise AuthError("Сессия недействительна, войдите заново") from error
