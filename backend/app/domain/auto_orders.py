"""Автоматические заявки на превентивное обслуживание.

Опасный прогноз порождает заявку. Пороги, коэффициент срока и тип работ лежат
в реестре направления, а не в этом файле. Спецификация §8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.meta import by_code
from app.tables import prediction, work_order

log = logging.getLogger(__name__)

AUTO_CREATED = "AUTO_CREATED"

# Заявка считается открытой, пока работа по ней не кончилась. Отклонённая
# заявка не запрещает новую: диспетчер отклонил заявку, а не признал объект
# исправным. Поток заявок останавливает отклонение самого прогноза.
CLOSED_STATUSES = ("DONE", "REJECTED")


@dataclass(frozen=True)
class Candidate:
    """Прогноз, по которому проверяется необходимость заявки."""

    id: str
    direction: str
    facility_id: str
    level: str
    horizon_hours: int
    computed_at: datetime


def next_number(conn: Connection) -> str:
    """Отдаёт следующий номер заявки вида `2026-0001`."""
    value = conn.execute(select(func.nextval("work_order_number_seq"))).scalar_one()
    return f"{datetime.now(tz=None).year}-{value:04d}"


def has_open_order(conn: Connection, facility_id: str, direction: str) -> bool:
    """Проверяет открытую заявку на том же объекте по тому же направлению."""
    found = conn.execute(
        select(work_order.c.id)
        .join(prediction, prediction.c.id == work_order.c.prediction_id)
        .where(
            work_order.c.facility_id == facility_id,
            prediction.c.direction == direction,
            work_order.c.status.notin_(CLOSED_STATUSES),
        )
        .limit(1)
    ).first()
    return found is not None


def create_for(conn: Connection, candidate: Candidate) -> str | None:
    """Создаёт заявку по прогнозу или отказывает.

    Отдаёт идентификатор заявки. Отдаёт None, когда заявка не нужна: уровень
    ниже порога, направление выключено или открытая заявка уже есть.
    """
    direction = by_code(candidate.direction)
    if direction is None:
        log.warning("направление вне реестра", extra={"direction": candidate.direction})
        return None

    if candidate.level not in direction.order_levels:
        return None

    if has_open_order(conn, candidate.facility_id, candidate.direction):
        return None

    # Половина горизонта, а не весь: заявка со сроком в конце горизонта
    # приезжает ровно к предполагаемой аварии.
    due_at = candidate.computed_at + timedelta(hours=candidate.horizon_hours * direction.due_factor)
    number = next_number(conn)
    order_id = f"WO-{number}"

    conn.execute(
        work_order.insert().values(
            id=order_id,
            number=number,
            prediction_id=candidate.id,
            facility_id=candidate.facility_id,
            work_type=direction.work_types[0],
            due_at=due_at,
            status=AUTO_CREATED,
            created_at=candidate.computed_at,
        )
    )
    log.info(
        "заявка создана автоматически",
        extra={
            "orderId": order_id,
            "predictionId": candidate.id,
            "direction": candidate.direction,
            "level": candidate.level,
        },
    )
    return order_id


def create_missing(conn: Connection, run_at: datetime) -> list[str]:
    """Создаёт заявки по всем прогнозам этого прогона.

    Повторный запуск в том же окне заявок не плодит: правило открытой заявки
    закрывает и это. Требование идемпотентности из §7.
    """
    rows = conn.execute(
        select(
            prediction.c.id,
            prediction.c.direction,
            prediction.c.facility_id,
            prediction.c.level,
            prediction.c.horizon_hours,
            prediction.c.computed_at,
        ).where(prediction.c.computed_at_bucket == run_at)
    ).all()

    created: list[str] = []
    for row in rows:
        order_id = create_for(conn, Candidate(*row))
        if order_id is not None:
            created.append(order_id)
    return created
