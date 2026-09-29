"""Автоматические заявки на превентивное обслуживание.

Опасный прогноз порождает заявку. Пороги, коэффициент срока и тип работ лежат
в реестре направления, а не в этом файле. Спецификация §8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.meta import by_code, level_order, mute_hours
from app.meta.directions import Direction
from app.tables import action_log, prediction, work_order

log = logging.getLogger(__name__)

AUTO_CREATED = "AUTO_CREATED"
CONFIRMED = "CONFIRMED"

BY_PIPELINE = "PIPELINE"
BY_DISPATCHER = "DISPATCHER"

# Заявка считается открытой, пока работа по ней не кончилась.
CLOSED_STATUSES = ("CLOSED_CONFIRMED", "CLOSED_NOT_CONFIRMED", "REJECTED")
REJECTED = "REJECTED"

# Действия конвейера над автозаявкой. Пишутся в журнал действий, как действия
# людей, но от имени конвейера. ADR 0020.
PIPELINE_ACTOR = "pipeline"
RELINK = "relink"
AUTO_REJECT = "reject"
RISK_DROPPED = "RISK_DROPPED"


@dataclass(frozen=True)
class Candidate:
    """Прогноз, по которому проверяется необходимость заявки."""

    id: str
    direction: str
    facility_id: str
    level: str
    horizon_hours: int
    computed_at: datetime


# Москва живёт в UTC+3 без перехода на летнее время.
MOSCOW = timedelta(hours=3)
DAILY = "daily"


def default_due(candidate: Candidate, direction: Direction) -> datetime:
    """Срок автозаявки до первого касания человеком.

    Суточный прогноз говорит о московских сутках, на которые он посчитан, и
    заявка должна успеть в эти сутки: срок стоит на их конце. Иначе прогноз
    полуночи давал срок в полдень, и к вечеру любая нетронутая заявка
    выглядела просроченной. Потоковый прогноз смотрит на 24 часа от момента
    расчёта, срок у него половина горизонта: заявка со сроком в конце
    горизонта приезжает ровно к предполагаемой аварии.
    """
    if direction.cadence == DAILY:
        local = candidate.computed_at.astimezone(UTC) + MOSCOW
        next_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return next_midnight - MOSCOW
    return candidate.computed_at + timedelta(hours=candidate.horizon_hours * direction.due_factor)


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


def create_for(conn: Connection, candidate: Candidate, created_by: str = BY_PIPELINE) -> str | None:
    """Создаёт заявку по прогнозу или отказывает.

    Отдаёт идентификатор заявки. Отдаёт None, когда заявка не нужна: уровень
    ниже порога, направление выключено или открытая заявка уже есть.

    Довод `created_by` различает происхождение. Конвейер создаёт заявку по
    порогу уровня, и она ждёт решения диспетчера в статусе `AUTO_CREATED`.
    Диспетчер создаёт заявку своим решением по прогнозу, и она рождается уже
    подтверждённой: подтверждать её второй раз значило бы спрашивать человека
    о том, что он только что решил. ADR 0006.
    """
    direction = by_code(candidate.direction)
    if direction is None:
        log.warning("направление вне реестра", extra={"direction": candidate.direction})
        return None

    by_dispatcher = created_by == BY_DISPATCHER
    status = CONFIRMED if by_dispatcher else AUTO_CREATED
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
    # назначает диспетчер при назначении бригады.
    due_at = default_due(candidate, direction)
    number = next_number(conn)
    order_id = f"WO-{number}"

    conn.execute(
        work_order.insert().values(
            id=order_id,
            number=number,
            prediction_id=candidate.id,
            facility_id=candidate.facility_id,
            direction=candidate.direction,
            work_type=direction.work_types[0],
            due_at=due_at,
            status=status,
            created_by=created_by,
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


def untouched_order(conn: Connection, candidate: Candidate) -> tuple[str, str] | None:
    """Автозаявка объекта и направления, которой человек ещё не касался.

    Отдаёт номер заявки и прогноз, которому она принадлежит. Это может быть и
    заявка самого прогноза: нетронутая карточка идёт за свежим прогнозом
    вместе со своей заявкой (ADR 0022).
    """
    found = conn.execute(
        select(work_order.c.id, work_order.c.prediction_id)
        .join(prediction, prediction.c.id == work_order.c.prediction_id)
        .where(
            work_order.c.facility_id == candidate.facility_id,
            prediction.c.direction == candidate.direction,
            work_order.c.status == AUTO_CREATED,
        )
        .order_by(work_order.c.created_at.desc())
        .limit(1)
    ).first()
    return (found.id, found.prediction_id) if found else None


def _log(conn: Connection, order_id: str, code: str, payload: dict[str, str]) -> None:
    conn.execute(
        action_log.insert().values(
            entity_type="order",
            entity_id=order_id,
            action_code=code,
            actor=PIPELINE_ACTOR,
            payload=payload,
        )
    )


def follow(conn: Connection, candidate: Candidate) -> str | None:
    """Ведёт нетронутую автозаявку объекта за свежим прогнозом. ADR 0020.

    Уровень свежего прогноза требует выезда: заявка переходит на него, срок
    считается от него. Уровень упал ниже порога: конвейер отклоняет заявку,
    потому что выезд по устаревшему риску не нужен. Заявку, которую диспетчер
    уже подтвердил или по которой назначил бригаду, конвейер не трогает:
    решение человека относится к прогнозу, который он видел.

    Отдаёт номер заявки, если что-то сделал.
    """
    direction = by_code(candidate.direction)
    found = untouched_order(conn, candidate)
    if direction is None or found is None:
        return None
    order_id, previous = found

    if candidate.level in direction.order_levels:
        due_at = default_due(candidate, direction)
        if previous == candidate.id:
            # Своя заявка: карточка переписана свежим прогнозом, срок за ним.
            conn.execute(
                work_order.update()
                .where(work_order.c.id == order_id, work_order.c.status == AUTO_CREATED)
                .values(due_at=due_at)
            )
            return order_id
        conn.execute(
            work_order.update()
            .where(work_order.c.id == order_id, work_order.c.status == AUTO_CREATED)
            .values(prediction_id=candidate.id, due_at=due_at)
        )
        _log(
            conn, order_id, RELINK, {"from": previous, "to": candidate.id, "level": candidate.level}
        )
        log.info(
            "автозаявка перешла на свежий прогноз",
            extra={"orderId": order_id, "predictionId": candidate.id, "riskLevel": candidate.level},
        )
        return order_id

    conn.execute(
        work_order.update()
        .where(work_order.c.id == order_id, work_order.c.status == AUTO_CREATED)
        .values(status=REJECTED)
    )
    _log(
        conn,
        order_id,
        AUTO_REJECT,
        {
            "reason": RISK_DROPPED,
            "comment": "Риск снизился по свежему прогнозу, выезд не нужен",
            "predictionId": candidate.id,
            "level": candidate.level,
        },
    )
    log.info(
        "автозаявка отклонена: риск снизился",
        extra={"orderId": order_id, "predictionId": candidate.id, "riskLevel": candidate.level},
    )
    return order_id


def create_missing(conn: Connection, run_at: datetime, run_id: int | None = None) -> list[str]:
    """Создаёт заявки по всем прогнозам этого прогона.

    Прогноз прогона это строка с его `run_id`: новая карточка или карточка
    происшествия, которую прогон обновил (ADR 0017). Без `run_id` прогнозом
    прогона считается строка с ключом `run_at`, как до ADR 0017.

    Повторный запуск в том же окне заявок не плодит: правило открытой заявки
    закрывает и это. Требование идемпотентности из §7.
    """
    condition = (
        prediction.c.run_id == run_id
        if run_id is not None
        else prediction.c.computed_at_bucket == run_at
    )
    rows = conn.execute(
        select(
            prediction.c.id,
            prediction.c.direction,
            prediction.c.facility_id,
            prediction.c.level,
            prediction.c.horizon_hours,
            prediction.c.computed_at,
        ).where(condition)
    ).all()

    created: list[str] = []
    for row in rows:
        candidate = Candidate(*row)
        # Нетронутая автозаявка объекта идёт за свежим прогнозом, а не рождает
        # вторую. ADR 0020.
        if follow(conn, candidate) is not None:
            continue
        order_id = create_for(conn, candidate)
        if order_id is not None:
            created.append(order_id)
    return created
