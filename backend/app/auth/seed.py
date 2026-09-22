"""Наборы демонстрационных учётных записей. По одной записи на каждую роль.

Стенд обязан подниматься одной командой, иначе проверяющий не увидит ни одного
экрана: без входа в систему не открывается ничего. Поэтому `python -m app.cli
seed` заводит первый набор — четыре записи, по одной на роль из ответа
заказчика 4.1.

**Набор, а не четыре записи навсегда.** Ключ второго фактора остаётся в том
приложении-аутентификаторе, где его завели, и один набор на всех означает, что
первый же проверяющий заведёт ключ у себя, а остальные в систему не войдут.
Панель тестового стенда заводит следующий набор кнопкой, и каждый берёт свой.

Логины первого набора идут без суффикса: `ods`, `district`, `tech`, `crew`.
Следующие получают номер: `ods-2`, `district-2` и так далее.

Пароль у всех один и приходит из переменной `SEED_PASSWORD`. Это демо, и
скрывать здесь нечего: панель стенда всё равно показывает его на экране.
Настоящие записи заводит команда `create-user`, а в проде они приходят из
каталога.

Второй фактор у новых записей не заведён намеренно. Первый вход каждой роли
проходит регистрацию ключа, и проверяющий видит весь путь: QR, сканирование
приложением-аутентификатором, подтверждение кодом.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Connection

from app.auth import directory
from app.auth.roles import SCOPE_COMPLEX, SCOPE_DISTRICT, require
from app.config import config
from app.tables import app_user, collector

FIRST_SET = 1


@dataclass(frozen=True)
class DemoRole:
    """Роль набора. Логин строится из основы и номера набора."""

    base: str
    full_name: str
    role: str


DEMO_ROLES: tuple[DemoRole, ...] = (
    DemoRole("ods", "Иванов И. И.", "ODS_DISPATCHER"),
    DemoRole("district", "Петров П. П.", "DISTRICT_DISPATCHER"),
    DemoRole("tech", "Сидоров С. С.", "TECHNICIAN"),
    DemoRole("crew", "Бригада 1", "RESPONSE_TEAM"),
)


def username_for(base: str, demo_set: int) -> str:
    """Логин набора. Первый идёт без суффикса: он же путь из README."""
    return base if demo_set == FIRST_SET else f"{base}-{demo_set}"


def _first_collector(conn: Connection) -> tuple[str | None, str | None]:
    """Первый коллектор посева и его район. Границы для узких ролей."""
    row = conn.execute(
        select(collector.c.code, collector.c.district).order_by(collector.c.code).limit(1)
    ).first()
    return (None, None) if row is None else (row.code, row.district)


def scope_for(role_code: str, complex_code: str | None, district: str | None) -> str | None:
    """Значение области видимости по виду, который назначен роли."""
    kind = require(role_code).scope_kind
    if kind == SCOPE_COMPLEX:
        return complex_code
    if kind == SCOPE_DISTRICT:
        return district
    return None


def set_numbers(conn: Connection) -> list[int]:
    """Номера существующих наборов по возрастанию."""
    rows = conn.execute(
        select(app_user.c.demo_set)
        .where(app_user.c.demo_set.isnot(None))
        .distinct()
        .order_by(app_user.c.demo_set)
    ).scalars()
    return [int(number) for number in rows]


def create_set(conn: Connection, demo_set: int | None = None) -> int:
    """Заводит набор и отдаёт его номер.

    Номер не назван значит берётся следующий свободный. Повторный вызов с тем
    же номером обновляет записи, а не плодит их.
    """
    if demo_set is None:
        highest = conn.execute(select(func.max(app_user.c.demo_set))).scalar()
        demo_set = int(highest or 0) + 1

    complex_code, district = _first_collector(conn)
    for item in DEMO_ROLES:
        username = username_for(item.base, demo_set)
        suffix = "" if demo_set == FIRST_SET else f" ({demo_set})"
        directory.upsert(
            conn,
            username=username,
            full_name=f"{item.full_name}{suffix}",
            role=item.role,
            password=config.seed_password,
            scope_value=scope_for(item.role, complex_code, district),
        )
        conn.execute(
            update(app_user).where(app_user.c.username == username).values(demo_set=demo_set)
        )
    return demo_set


def delete_set(conn: Connection, demo_set: int) -> int:
    """Удаляет набор целиком. Отдаёт число удалённых записей.

    Настоящие записи не трогаются: условие идёт по `demo_set`, а у них оно
    пустое.
    """
    result = conn.execute(delete(app_user).where(app_user.c.demo_set == demo_set))
    return int(result.rowcount)


def reset_keys(conn: Connection, demo_set: int | None = None) -> int:
    """Снимает заведённые ключи второго фактора у демонстрационных записей.

    Нужно, когда телефон проверяющего ушёл вместе с проверяющим. Следующий вход
    начнёт регистрацию заново.
    """
    statement = update(app_user).values(totp_secret=None, mfa_enrolled=False)
    statement = statement.where(
        app_user.c.demo_set == demo_set if demo_set is not None else app_user.c.demo_set.isnot(None)
    )
    return int(conn.execute(statement).rowcount)


def seed_users(conn: Connection) -> list[str]:
    """Заводит первый набор. Повторный вызов обновляет его."""
    create_set(conn, FIRST_SET)
    return [username_for(item.base, FIRST_SET) for item in DEMO_ROLES]
