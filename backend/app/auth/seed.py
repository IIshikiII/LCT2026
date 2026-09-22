"""Демонстрационные учётные записи. По одной на каждую роль.

Стенд обязан подниматься одной командой, иначе проверяющий не увидит ни одного
экрана: без входа в систему не открывается ничего. Поэтому `python -m app.cli
seed` заводит четыре записи, по одной на роль из ответа заказчика 4.1.

Пароль у всех четырёх один и приходит из переменной `SEED_PASSWORD`. Это демо,
и скрывать здесь нечего: настоящие записи заводит команда `create-user`, а в
проде записи приходят из каталога.

Второй фактор у демонстрационных записей не заведён намеренно. Первый вход
каждой роли проходит регистрацию ключа, и проверяющий видит весь путь: секрет,
ссылку `otpauth://`, ввод кода из своего аутентификатора.

Область видимости привязана к данным посева. Техник получает первый коллектор,
диспетчер района — район этого коллектора. Если посев данных ещё не прошёл,
границы остаются пустыми, и обе роли не видят ничего: это верно, потому что
техник без комплекса заведён с ошибкой.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.auth import directory
from app.auth.roles import SCOPE_COMPLEX, SCOPE_DISTRICT, require
from app.config import config
from app.tables import collector


@dataclass(frozen=True)
class DemoUser:
    username: str
    full_name: str
    role: str


DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser("ods", "Иванов И. И.", "ODS_DISPATCHER"),
    DemoUser("district", "Петров П. П.", "DISTRICT_DISPATCHER"),
    DemoUser("tech", "Сидоров С. С.", "TECHNICIAN"),
    DemoUser("crew", "Бригада 1", "RESPONSE_TEAM"),
)


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


def seed_users(conn: Connection) -> list[str]:
    """Заводит демонстрационные записи. Повторный вызов обновляет их."""
    complex_code, district = _first_collector(conn)
    for user in DEMO_USERS:
        directory.upsert(
            conn,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            password=config.seed_password,
            scope_value=scope_for(user.role, complex_code, district),
        )
    return [user.username for user in DEMO_USERS]
