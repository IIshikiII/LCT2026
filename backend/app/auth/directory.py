"""Каталог пользователей. Стоит на месте Active Directory.

Заказчик держит учётные записи в каталоге, совместимом с LDAP (QA-сессия), и
отдать его нам не может по требованиям безопасности (QA-сессия). Там же он
сказал, что эмуляция внешних систем является ожидаемой частью решения.

Поэтому каталог здесь свой, а место стыковки названо явно. Поле `directory`
учётной записи принимает `LOCAL` или `LDAP`. Пароль записи `LOCAL` проверяет
эта функция, пароль записи `LDAP` проверял бы вызов `bind` к каталогу, и
менять пришлось бы только `authenticate`. Всё остальное — роль, область
видимости, второй фактор — от источника записи не зависит.

Второй фактор каталогу не отдаётся ни в одном из двух случаев. Домен отвечает
за то, кто человек, а одноразовый код — за то, что за клавиатурой он сам.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from app.auth import passwords
from app.auth.roles import SCOPE_ALL, Role, require
from app.tables import app_user

LOCAL = "LOCAL"
LDAP = "LDAP"


@dataclass(frozen=True)
class Account:
    """Учётная запись целиком. Наружу уходит не она, а `Actor`."""

    username: str
    full_name: str
    role: Role
    scope_kind: str
    scope_value: str | None
    password_hash: str | None
    totp_secret: str | None
    mfa_enrolled: bool
    is_active: bool
    directory: str


def _account(row: object) -> Account:
    return Account(
        username=row.username,  # type: ignore[attr-defined]
        full_name=row.full_name,  # type: ignore[attr-defined]
        role=require(row.role),  # type: ignore[attr-defined]
        scope_kind=row.scope_kind,  # type: ignore[attr-defined]
        scope_value=row.scope_value,  # type: ignore[attr-defined]
        password_hash=row.password_hash,  # type: ignore[attr-defined]
        totp_secret=row.totp_secret,  # type: ignore[attr-defined]
        mfa_enrolled=row.mfa_enrolled,  # type: ignore[attr-defined]
        is_active=row.is_active,  # type: ignore[attr-defined]
        directory=row.directory,  # type: ignore[attr-defined]
    )


def find(conn: Connection, username: str) -> Account | None:
    row = conn.execute(select(app_user).where(app_user.c.username == username)).first()
    return None if row is None else _account(row)


def authenticate(conn: Connection, username: str, password: str) -> Account | None:
    """Проверяет логин и пароль. Отдаёт запись или `None`.

    Причину отказа функция не называет. Разные ответы на «нет такого логина» и
    «пароль не тот» дают перебор имён: злоумышленник узнаёт список сотрудников,
    не зная ни одного пароля.
    """
    account = find(conn, username)
    if account is None or not account.is_active:
        # Хеш всё равно считается: иначе ответ на несуществующий логин приходит
        # за микросекунды, а на существующий за шестьдесят миллисекунд, и
        # список логинов читается по времени ответа.
        passwords.hash_password(password)
        return None
    if not account.password_hash or not passwords.verify_password(password, account.password_hash):
        return None
    return account


def enroll(conn: Connection, username: str, secret: str) -> None:
    """Записывает секрет второго фактора и отмечает регистрацию ключа."""
    conn.execute(
        update(app_user)
        .where(app_user.c.username == username)
        .values(totp_secret=secret, mfa_enrolled=True)
    )


def mark_login(conn: Connection, username: str) -> None:
    conn.execute(
        update(app_user)
        .where(app_user.c.username == username)
        .values(last_login_at=datetime.now(UTC))
    )


def upsert(
    conn: Connection,
    username: str,
    full_name: str,
    role: str,
    password: str,
    scope_value: str | None = None,
    directory: str = LOCAL,
) -> None:
    """Заводит или обновляет учётную запись.

    Вид области видимости берётся у роли, а не у вызывающего: техник не может
    видеть предприятие целиком по недосмотру того, кто заводит запись.
    """
    known = require(role)
    values = {
        "full_name": full_name,
        "role": known.code,
        "scope_kind": known.scope_kind,
        "scope_value": None if known.scope_kind == SCOPE_ALL else scope_value,
        "password_hash": passwords.hash_password(password),
        "is_active": True,
        "directory": directory,
    }
    updated = conn.execute(update(app_user).where(app_user.c.username == username).values(**values))
    if updated.rowcount == 0:
        conn.execute(
            app_user.insert().values(username=username, created_at=datetime.now(UTC), **values)
        )
