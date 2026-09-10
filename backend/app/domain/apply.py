"""Применение действия: проверка, смена статуса, аудит.

Оба эндпоинта действий проходят через эти функции, поэтому правила одинаковы
для прогноза и для заявки.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import Table, update
from sqlalchemy.engine import Connection

from app.domain import transitions
from app.domain.validation import Invalid, validate
from app.schemas import ActionDef
from app.tables import action_log


def pick(actions: list[ActionDef], code: str) -> ActionDef | None:
    return next((item for item in actions if item.code == code), None)


def check_body(actions: list[ActionDef], code: str, body: dict[str, Any]) -> None:
    """Проверяет тело описанием формы этого действия."""
    action = pick(actions, code)
    if action is None:
        return
    try:
        validate(action, body)
    except Invalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def next_status(entity: str, code: str, status: str) -> str:
    """Отдаёт следующий статус или отказывает.

    Неизвестный код даёт 404: такого действия не существует. Молчаливая смена
    статуса по неизвестному коду скрывала бы опечатку и устаревшего клиента.

    Известный код в неподходящем статусе даёт 409. Сущность есть, тело верное,
    но переход запрещён.
    """
    move = transitions.find(entity, code)
    if move is None:
        known = ", ".join(
            sorted({item.code for item in transitions.TRANSITIONS if item.entity == entity})
        )
        raise HTTPException(
            status_code=404,
            detail=f"действия {code} не существует, известны: {known}",
        )

    if status not in move.from_statuses:
        allowed = ", ".join(transitions.codes_for(entity, status)) or "нет доступных действий"
        raise HTTPException(
            status_code=409,
            detail=f"действие {code} недоступно в статусе {status}: {allowed}",
        )
    return move.to_status


def set_status(
    conn: Connection, table: Table, entity_id: str, expected: str, new_status: str, **extra: Any
) -> None:
    """Меняет статус с проверкой прежнего значения.

    Условие по прежнему статусу закрывает гонку двух диспетчеров: второе
    нажатие не перезапишет результат первого молча.
    """
    if new_status == expected and not extra:
        return
    result = conn.execute(
        update(table)
        .where(table.c.id == entity_id, table.c.status == expected)
        .values(status=new_status, **extra)
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail=f"состояние изменилось, повторите: {entity_id}",
        )


def log(conn: Connection, entity: str, entity_id: str, code: str, body: dict[str, Any]) -> None:
    """Пишет действие в аудит. Тело сохраняется целиком: другого следа нет."""
    conn.execute(
        action_log.insert().values(
            entity_type=entity,
            entity_id=entity_id,
            action_code=code,
            actor="dispatcher",
            payload=body,
        )
    )
