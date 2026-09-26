"""Учётные записи фикстуры и подмена того, кто выполняет запрос.

Живёт отдельным модулем, а не в `conftest.py`, потому что нужен и фикстурам, и
телу теста. Тест заявки переключается на группу реагирования посреди сценария:
диспетчер назначает бригаду, закрывает заявку бригада, и это разные роли.
"""

from __future__ import annotations

from app.auth import passwords
from app.auth.actor import Actor
from app.auth.deps import current_actor
from app.auth.roles import require
from app.main import app as fastapi_app

# Пароль учётных записей фикстуры. Хеш считается один раз на процесс: scrypt
# стоит около 60 мс, и пересчёт его в каждом тесте добавил бы минуту к прогону.
TEST_PASSWORD = "test-pass"
TEST_PASSWORD_HASH = passwords.hash_password(TEST_PASSWORD)

# Секрет второго фактора фикстуры. Постоянный, чтобы тест считал по нему код.
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"

# Роль по умолчанию. Ею подписаны все запросы, пока тест не сказал иначе.
DEFAULT_USER = "ods"

USERS: list[tuple[str, str, str, str | None, bool]] = [
    # логин, имя, роль, значение области видимости, заведён ли второй фактор
    ("ods", "Иванов И. И.", "ODS_DISPATCHER", None, True),
    ("district", "Петров П. П.", "DISTRICT_DISPATCHER", "CAO", True),
    ("tech", "Сидоров С. С.", "TECHNICIAN", "K-CAO-1", True),
    ("crew", "Бригада 1", "RESPONSE_TEAM", None, True),
    # Запись без второго фактора: первый вход проходит регистрацию ключа.
    ("fresh", "Новиков Н. Н.", "ODS_DISPATCHER", None, False),
]


def actor_named(username: str) -> Actor:
    """Собирает `Actor` по строке из `USERS`."""
    _, full_name, role_code, scope_value, _ = next(item for item in USERS if item[0] == username)
    role = require(role_code)
    return Actor(
        username=username,
        full_name=full_name,
        role=role,
        scope_kind=role.scope_kind,
        scope_value=scope_value,
    )


def sign_in(username: str) -> None:
    """Подписывает следующие запросы теста этой учётной записью."""
    fastapi_app.dependency_overrides[current_actor] = lambda: actor_named(username)


def sign_out() -> None:
    fastapi_app.dependency_overrides.pop(current_actor, None)
