"""Проверка тела действия по описанию формы.

Сервер проверяет тело тем же описанием `fields`, которым фронт рисовал форму.
Источник правды один: правка поля меняет и форму, и проверку.
"""

from __future__ import annotations

from typing import Any

from app.schemas import ActionDef, FieldDef


class Invalid(Exception):
    """Тело не прошло проверку. Роутер отдаёт 422 с этим текстом."""


def _check(field: FieldDef, body: dict[str, Any]) -> None:
    present = field.name in body and body[field.name] not in (None, "")

    if field.required and not present:
        raise Invalid(f"поле {field.name} обязательно")
    if not present:
        return

    value = body[field.name]

    if field.type == "boolean":
        # Строка "true" подтверждением не считается. От этого поля зависят
        # Precision и Recall, поэтому приведение типа здесь запрещено.
        if not isinstance(value, bool):
            raise Invalid(f"поле {field.name} принимает только true или false")
        return

    if field.min_length is not None and (
        not isinstance(value, str) or len(value.strip()) < field.min_length
    ):
        raise Invalid(f"поле {field.name} короче {field.min_length} знаков")


def validate(action: ActionDef, body: dict[str, Any]) -> None:
    for field in action.fields:
        _check(field, body)
