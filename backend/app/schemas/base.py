"""Общая база DTO. Все ответы уходят в camelCase."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Dto(BaseModel):
    """База ответа.

    Поля пишутся в snake_case, а наружу уходят в camelCase. Фронт ждёт именно
    camelCase. Отдавать ответы только через `model_dump(by_alias=True)`.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Page[ItemT](Dto):
    """Конверт списка. Одинаков для всех списочных эндпоинтов."""

    items: list[ItemT]
    page: int
    page_size: int
    total: int
