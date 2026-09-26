"""DTO потока СМВУ. ADR 0017.

Строка потока держит имена полей журнала заказчика: так её шлёт СМВУ, и так
её шлёт заглушка. Поле `тревожное` принимает `true`, `false`, `t`, `f`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import Dto


class StreamEvent(BaseModel):
    """Строка журнала СМВУ."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: int | None = Field(default=None, alias="ид_события")
    channel_id: int = Field(alias="ид_канала_данных")
    day: str = Field(alias="дата", pattern=r"^\d{4}-\d{2}-\d{2}$")
    clock: str = Field(alias="время", pattern=r"^\d{2}:\d{2}:\d{2}$")
    alarm: bool | str = Field(alias="тревожное")
    value: str | None = Field(default=None, alias="значение_датчика")


class StreamBatch(BaseModel):
    """Пачка строк. Заглушка шлёт пачку раз в несколько секунд."""

    events: list[StreamEvent] = Field(max_length=20000)


class StreamAccepted(Dto):
    """Итог приёма пачки."""

    received: int
    events: int
    readings: int
    skipped: int
