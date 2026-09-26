"""Аутентификация, роли и области видимости. ADR 0007.

Точка входа для остального кода. Роутеры берут отсюда `CurrentActor` и
`check_permission`, запросы берут `scope`.
"""

from __future__ import annotations

from app.auth.actor import Actor
from app.auth.deps import CurrentActor, check_permission, current_actor
from app.auth.roles import ROLES, Role, by_code, require

__all__ = [
    "ROLES",
    "Actor",
    "CurrentActor",
    "Role",
    "by_code",
    "check_permission",
    "current_actor",
    "require",
]
