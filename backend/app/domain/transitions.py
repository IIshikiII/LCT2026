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


OPEN = ("NEW", "IN_REVIEW")

# Целевой статус прогноза у действия `decide` зависит от уровня, который
# назвал диспетчер: выезд нужен значит `ORDER_OPEN`, иначе `DECIDED`. Поэтому в
# таблице он записан как `ORDER_OPEN`, а роутер подменяет его на `DECIDED` по
# правилу `catalog.needs_order`. ADR 0006, раздел 2.
TRANSITIONS: tuple[Transition, ...] = (
    Transition("take", PREDICTION, ("NEW",), "IN_REVIEW"),
    Transition("release", PREDICTION, ("IN_REVIEW",), "NEW"),
    Transition("decide", PREDICTION, OPEN, "ORDER_OPEN"),
    Transition("confirm", ORDER, ("AUTO_CREATED", "MANUAL_CREATED"), "CONFIRMED"),
    Transition("reject", ORDER, ("AUTO_CREATED", "MANUAL_CREATED"), "REJECTED"),
    Transition("start", ORDER, ("CONFIRMED",), "IN_PROGRESS"),
    Transition("close", ORDER, ("IN_PROGRESS",), "CLOSED_CONFIRMED"),
)

# Итог закрытия заявки меняет и её статус, и статус прогноза. Ключ — значение
# поля `factConfirmed` формы закрытия.
CLOSE_OUTCOME: dict[bool, str] = {
    True: "CLOSED_CONFIRMED",
    False: "CLOSED_NOT_CONFIRMED",
}


def find(entity: str, code: str) -> Transition | None:
    return next(
        (item for item in TRANSITIONS if item.entity == entity and item.code == code),
        None,
    )


def codes_for(entity: str, status: str) -> tuple[str, ...]:
    return tuple(
        item.code for item in TRANSITIONS if item.entity == entity and status in item.from_statuses
    )
