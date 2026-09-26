"""Зависимости FastAPI: кто выполняет запрос и можно ли ему это.

Проверка стоит на каждом маршруте, а не в одном месте посередине. Причина в
том, что забытый маршрут в списке исключений открывает данные молча, и увидеть
это по коду нельзя. Поэтому список открытых маршрутов короткий и записан явно,
а тест `tests/test_auth_guard.py` обходит все маршруты приложения и падает,
если у защищённого нет зависимости.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Connection

from app.auth import directory
from app.auth.actor import Actor
from app.auth.service import AuthError, actor_from_token

# `auto_error=False` оставляет ответ за нами: FastAPI отдал бы 403 без
# заголовка `WWW-Authenticate`, а на отсутствующий токен положено 401.
bearer = HTTPBearer(auto_error=False, description="Токен из POST /auth/mfa")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Actor:
    """Тот, кто выполняет запрос. Отказ 401, если токена нет или он негоден."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Требуется вход в систему")
    try:
        actor = actor_from_token(credentials.credentials)
    except AuthError as error:
        raise _unauthorized(str(error)) from error
    # Имя попадает в строку журнала доступа: ТЗ §11 требует связывать действие
    # с человеком, а не только с адресом.
    request.state.actor = actor.username
    return actor


CurrentActor = Annotated[Actor, Depends(current_actor)]


def check_permission(conn: Connection, actor: Actor, action_code: str) -> None:
    """Пускает действие или отказывает 403.

    Здесь же проверяется, что запись не отключили после выдачи токена. Токен
    не отзывается и живёт до конца смены, поэтому у чтения отключённая запись
    доживает до истечения токена, а у действия — нет. Чтений идёт по одному в
    минуту на экран, действий единицы за смену, поэтому лишний запрос к базе
    стоит ровно там, где он что-то меняет.
    """
    if not actor.may(action_code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"роль «{actor.role.label}» не выполняет действие {action_code}",
        )
    account = directory.find(conn, actor.username)
    if account is None or not account.is_active:
        raise _unauthorized("Учётная запись отключена")
