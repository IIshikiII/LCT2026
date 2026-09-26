"""Тот, кто выполняет запрос. Собирается из токена, в базу не ходит.

`Actor` носит ровно то, что нужно для решения о доступе: имя, роль, границы
видимости. Пароль, секрет второго фактора и прочие поля учётной записи сюда не
попадают: они нужны только при входе.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.auth.roles import Role, require


@dataclass(frozen=True)
class Actor:
    username: str
    full_name: str
    role: Role
    scope_kind: str
    scope_value: str | None

    def may(self, action_code: str) -> bool:
        """Имеет ли роль право на это действие."""
        return action_code in self.role.permissions

    def claims(self) -> dict[str, Any]:
        """Полезная нагрузка токена. Зеркало `from_claims`."""
        return {
            "sub": self.username,
            "name": self.full_name,
            "role": self.role.code,
            "scopeKind": self.scope_kind,
            "scopeValue": self.scope_value,
        }


def from_claims(payload: dict[str, Any]) -> Actor:
    """Собирает `Actor` из проверенного токена.

    Роль берётся по коду через справочник, а не из полей токена. Права в токен
    не кладутся: иначе список прав застыл бы на момент входа, и правка
    справочника не действовала бы до конца смены.
    """
    return Actor(
        username=str(payload.get("sub", "")),
        full_name=str(payload.get("name", "")),
        role=require(str(payload.get("role", ""))),
        scope_kind=str(payload.get("scopeKind", "")),
        scope_value=payload.get("scopeValue"),
    )
