"""Роутер входа в систему. ADR 0007.

Четыре эндпоинта закрывают весь путь: пароль, код, кто я, выход. Пятого не
нужно: регистрация ключа идёт тем же вторым шагом, что и обычный вход.

Первые два эндпоинта открыты, остальные требуют токен. Список открытых
маршрутов лежит в `app/api/__init__.py` и проверяется тестом.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.engine import Connection

from app.auth import service
from app.auth.actor import Actor
from app.auth.deps import CurrentActor
from app.auth.service import AuthError, Session
from app.db import get_tx
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    MfaRequest,
    SessionResponse,
)

router = APIRouter(tags=["auth"], prefix="/auth")

Tx = Annotated[Connection, Depends(get_tx)]


def _denied(error: AuthError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=str(error),
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user(actor: Actor) -> CurrentUser:
    return CurrentUser(
        username=actor.username,
        full_name=actor.full_name,
        role=actor.role.code,
        role_label=actor.role.label,
        scope_kind=actor.scope_kind,
        scope_value=actor.scope_value,
        permissions=sorted(actor.role.permissions),
    )


def _session(result: Session) -> SessionResponse:
    return SessionResponse(
        access_token=result.token,
        expires_in=result.expires_in,
        user=_user(result.actor),
    )


@router.post("/login", response_model=LoginResponse, response_model_by_alias=True)
def login(body: LoginRequest, conn: Tx) -> LoginResponse:
    """Первый шаг. Токена сессии здесь нет: пароля мало."""
    try:
        result = service.login(conn, body.username.strip(), body.password)
    except AuthError as error:
        raise _denied(error) from error
    return LoginResponse(
        status=result.status,
        mfa_token=result.mfa_token or "",
        secret=result.secret,
        otpauth_url=result.otpauth_url,
    )


@router.post("/mfa", response_model=SessionResponse, response_model_by_alias=True)
def mfa(body: MfaRequest, conn: Tx) -> SessionResponse:
    """Второй шаг. Он же подтверждает только что заведённый ключ."""
    try:
        return _session(service.confirm(conn, body.mfa_token, body.code))
    except AuthError as error:
        raise _denied(error) from error


@router.get("/me", response_model=CurrentUser, response_model_by_alias=True)
def me(actor: CurrentActor) -> CurrentUser:
    """Кто вошёл. Фронт зовёт её при запуске, чтобы восстановить сессию."""
    return _user(actor)


@router.post("/logout")
def logout(actor: CurrentActor, conn: Tx) -> dict[str, str]:
    """Выход. Токен снимает браузер, сервер записывает событие.

    Списка отозванных токенов нет намеренно: он завёл бы состояние ради
    нескольких часов, которые токен и так живёт. ADR 0007.
    """
    service.audit(conn, actor.username, "logout")
    return {"status": "ok"}
