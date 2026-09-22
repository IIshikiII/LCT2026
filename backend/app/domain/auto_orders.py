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

from app.meta import by_code, level_order, mute_hours
from app.tables import prediction, work_order

log = logging.getLogger(__name__)

AUTO_CREATED = "AUTO_CREATED"
MANUAL_CREATED = "MANUAL_CREATED"

# Заявка считается открытой, пока работа по ней не кончилась.
CLOSED_STATUSES = ("CLOSED_CONFIRMED", "CLOSED_NOT_CONFIRMED", "REJECTED")


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


def suppress_until(reason: str | None, direction_code: str, at: datetime) -> datetime:
    """Считает момент снятия мьюта, когда диспетчер не назвал свой.

    Длина следует из причины отклонения: дубль живёт сутки, особенность
    объекта — месяц. Запасное значение лежит в записи направления.
    """
    direction = by_code(direction_code)
    fallback = direction.reject_cooldown_hours if direction else 168
    return at + timedelta(hours=mute_hours(reason, fallback))


def suppressed_level(conn: Connection, facility_id: str, direction: str, at: datetime) -> int:
    """Отдаёт наибольший уровень, заглушённый на объекте на этот момент.

    Отклонение — суждение о текущем состоянии, а не приговор объекту. Поэтому
    мьют истекает по времени и снимается сразу, как только уровень вырос: отказ
    на HIGH ничего не говорит о CRITICAL.
    """
    rows = (
        conn.execute(
            select(prediction.c.level).where(
                prediction.c.facility_id == facility_id,
                prediction.c.direction == direction,
                prediction.c.suppress_until > at,
            )
        )
        .scalars()
        .all()
    )
    return max((level_order(level) for level in rows), default=0)


def create_for(
    conn: Connection, candidate: Candidate, status: str = AUTO_CREATED
) -> str | None:
    """Создаёт заявку по прогнозу или отказывает.

    Отдаёт идентификатор заявки. Отдаёт None, когда заявка не нужна: уровень
    ниже порога, направление выключено или открытая заявка уже есть.

    Довод `status` различает два происхождения заявки. Конвейер создаёт
    `AUTO_CREATED` по порогу уровня. Диспетчер создаёт `MANUAL_CREATED`, когда
    поднял уровень прогноза выше предложенного моделью, и тогда проверка порога
    не нужна: решение уже принял человек. ADR 0006.
    """
    direction = by_code(candidate.direction)
    if direction is None:
        log.warning("направление вне реестра", extra={"direction": candidate.direction})
        return None

    by_dispatcher = status == MANUAL_CREATED
    if not by_dispatcher and candidate.level not in direction.order_levels:
        return None

    # Правило «одна открытая заявка на объект и направление» бережёт диспетчера
    # от потока автозаявок. К решению человека оно не применяется: диспетчер
    # сказал, что выезд нужен, и статус прогноза обязан этому соответствовать.
    # Иначе прогноз ушёл бы в ORDER_OPEN без заявки, и карточка показала бы
    # «Заявка в работе» при пустой ссылке.
    if not by_dispatcher and has_open_order(conn, candidate.facility_id, candidate.direction):
        return None

    if not by_dispatcher and level_order(candidate.level) <= suppressed_level(
        conn, candidate.facility_id, candidate.direction, candidate.computed_at
    ):
        return None

    # Срок по умолчанию, до первого касания человеком. Настоящий срок
    # назначает диспетчер при подтверждении заявки. Половина горизонта, а не
    # весь: заявка со сроком в конце горизонта приезжает ровно к
    # предполагаемой аварии.
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
            status=status,
            created_at=candidate.computed_at,
        )
    )
    log.info(
        "заявка создана диспетчером" if by_dispatcher else "заявка создана автоматически",
        extra={
            "orderId": order_id,
            "predictionId": candidate.id,
            "direction": candidate.direction,
            # Не `level`: это имя занимает серьёзность самой записи.
            "riskLevel": candidate.level,
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
