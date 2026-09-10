"""Таблица переходов. Данные, а не ветвление по коду действия.

Добавить действие — значит добавить запись сюда и в `app/domain/actions.py`.
Роутер ищет запись по коду и не разбирает коды через `if`.
"""

from __future__ import annotations

from dataclasses import dataclass

PREDICTION = "prediction"
ORDER = "order"


@dataclass(frozen=True)
class Transition:
    code: str
    entity: str
    from_statuses: tuple[str, ...]
    to_status: str


TRANSITIONS: tuple[Transition, ...] = (
    Transition("confirm_order", PREDICTION, ("NEW", "IN_REVIEW"), "ORDER_CONFIRMED"),
    Transition("inspect", PREDICTION, ("NEW", "IN_REVIEW"), "IN_REVIEW"),
    Transition("reject", PREDICTION, ("NEW", "IN_REVIEW", "ORDER_CONFIRMED"), "REJECTED"),
    Transition("confirm", ORDER, ("AUTO_CREATED",), "CONFIRMED"),
    Transition("reject", ORDER, ("AUTO_CREATED",), "REJECTED"),
    Transition("start", ORDER, ("CONFIRMED",), "IN_PROGRESS"),
    Transition("close", ORDER, ("IN_PROGRESS",), "DONE"),
)


def find(entity: str, code: str) -> Transition | None:
    return next(
        (item for item in TRANSITIONS if item.entity == entity and item.code == code),
        None,
    )


def codes_for(entity: str, status: str) -> tuple[str, ...]:
    return tuple(
        item.code for item in TRANSITIONS if item.entity == entity and status in item.from_statuses
    )
