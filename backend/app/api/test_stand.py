"""Панель тестового стенда: наборы учётных записей для жюри. ADR 0007.

**Эти эндпоинты открыты и раздают логины с паролями без входа в систему.** Так
задумано: панель рисуется на экране входа, и человек, который ещё не вошёл,
обязан увидеть, чем войти. Ради этого и существует флаг `TEST_STAND`.

Пока флаг выключен, список приходит пустым с признаком `enabled: false`, а
создание и удаление отвечают 404. Включённый флаг в промышленной установке
делает вход бессмысленным, и в `.env.example` он выключен.

Набор это четыре записи, по одной на роль. Ключ второго фактора остаётся в том
приложении-аутентификаторе, где его завели, поэтому один набор на всех не
годится: первый же проверяющий заведёт ключ у себя. Кнопка «создать набор»
выдаёт следующему свой комплект.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.auth import seed
from app.auth.roles import require
from app.config import config
from app.db import get_conn, get_tx
from app.schemas.auth import TestAccount, TestAccountSet, TestStand
from app.tables import app_user

router = APIRouter(tags=["test-stand"], prefix="/auth/test-accounts")

# Порядок ролей в наборе. Его же держит панель на экране.
ROLE_ORDER = {item.role: index for index, item in enumerate(seed.DEMO_ROLES)}

Reader = Annotated[Connection, Depends(get_conn)]
Writer = Annotated[Connection, Depends(get_tx)]


def _require_flag() -> None:
    """Отказывает 404, когда стенд выключен.

    Именно 404, а не 403: выключенного стенда для клиента не существует, и
    сообщать о нём нечего.
    """
    if not config.test_stand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="режим тестового стенда выключен",
        )


def _collect(conn: Connection) -> TestStand:
    """Собирает наборы. Выключенный стенд отдаёт пустой список."""
    if not config.test_stand:
        return TestStand(enabled=False, password="", sets=[])

    rows = conn.execute(
        select(app_user).where(app_user.c.demo_set.isnot(None)).order_by(app_user.c.demo_set)
    ).all()

    grouped: dict[int, list[TestAccount]] = {}
    for row in rows:
        role = require(row.role)
        grouped.setdefault(int(row.demo_set), []).append(
            TestAccount(
                username=row.username,
                full_name=row.full_name,
                role=role.code,
                role_label=role.label,
                scope_kind=row.scope_kind,
                scope_value=row.scope_value,
                mfa_enrolled=bool(row.mfa_enrolled),
            )
        )

    # Порядок ролей внутри набора задаёт реестр, а не база: от самого широкого
    # обзора к самому узкому, бригада последней. Сортировка по имени или по
    # моменту создания давала бы разный порядок от стенда к стенду.
    for accounts in grouped.values():
        accounts.sort(key=lambda item: ROLE_ORDER.get(item.role, len(ROLE_ORDER)))

    return TestStand(
        enabled=True,
        # Пароль один на все наборы. Панель всё равно показывает его рядом с
        # каждым логином, поэтому хранить разные незачем.
        password=config.seed_password,
        sets=[TestAccountSet(set=number, accounts=grouped[number]) for number in sorted(grouped)],
    )


@router.get("", response_model=TestStand, response_model_by_alias=True)
def list_sets(conn: Reader) -> TestStand:
    """Наборы учётных записей. Открыт всегда, наполняется только при флаге."""
    return _collect(conn)


@router.post("", response_model=TestStand, response_model_by_alias=True)
def add_set(conn: Writer) -> TestStand:
    """Заводит следующий набор и отдаёт список целиком."""
    _require_flag()
    seed.create_set(conn)
    return _collect(conn)


@router.delete("/{demo_set}", response_model=TestStand, response_model_by_alias=True)
def remove_set(demo_set: int, conn: Writer) -> TestStand:
    """Удаляет набор целиком. Настоящие записи условие не задевает."""
    _require_flag()
    if seed.delete_set(conn, demo_set) == 0:
        raise HTTPException(status_code=404, detail=f"набора {demo_set} нет")
    return _collect(conn)
