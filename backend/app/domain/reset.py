"""Возврат демонстрационных данных в исходное состояние.

На стенде проверяющие разбирают прогнозы, назначают бригады и закрывают
заявки. Следующему проверяющему нужен чистый журнал, иначе половина строк уже
кем-то отработана, а «Взять в работу» нет ни у одной.

Пересчитывать конвейер ради этого незачем: прогнозы те же, изменились только
следы работы над ними. Сброс снимает эти следы одной транзакцией.

Чего сброс не трогает: сами прогнозы, объекты, события доступа, прогоны
конвейера, замеры моделей и учётные записи. Заведённые ключи второго фактора
тоже остаются на месте, иначе жюри пришлось бы сканировать QR заново после
каждого сброса.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import Connection

from app.tables import action_log, prediction, work_order

# Статус, с которого прогноз начинает жизнь. Его же ставит конвейер.
FRESH_PREDICTION = "NEW"

# Статус автозаявки сразу после создания конвейером.
FRESH_ORDER = "AUTO_CREATED"


@dataclass(frozen=True)
class ResetResult:
    predictions: int
    orders: int
    log_entries: int


def reset_demo(conn: Connection) -> ResetResult:
    """Снимает следы работы диспетчеров и бригад. Отдаёт числа для отчёта."""
    predictions = conn.execute(
        update(prediction)
        .where(prediction.c.status != FRESH_PREDICTION)
        .values(
            status=FRESH_PREDICTION,
            assignee=None,
            verdict=None,
            dispatcher_level=None,
            decided_at=None,
            suppress_until=None,
        )
    ).rowcount

    # Мьют мог стоять и на прогнозе, который остался новым: отклонённая заявка
    # ставит его, не трогая статус.
    conn.execute(
        update(prediction)
        .where(prediction.c.suppress_until.isnot(None))
        .values(suppress_until=None)
    )

    orders = conn.execute(
        update(work_order)
        .where(work_order.c.status != FRESH_ORDER)
        .values(status=FRESH_ORDER, outcome=None)
    ).rowcount

    # Заявки, созданные решением диспетчера, исчезают вместе с решением: без
    # него у них нет основания. Заявки конвейера остаются.
    manual = select(work_order.c.id).where(work_order.c.created_by != "PIPELINE")
    orders += conn.execute(delete(work_order).where(work_order.c.id.in_(manual))).rowcount

    entries = conn.execute(
        delete(action_log).where(action_log.c.entity_type.in_(("prediction", "order")))
    ).rowcount

    return ResetResult(predictions=int(predictions), orders=int(orders), log_entries=int(entries))


def reset_predictions(conn: Connection) -> ResetResult:
    """Удаляет прогнозы, заявки и их журнал действий. Отдаёт числа для отчёта.

    После этого первый прогон конвейера считает все направления заново, как
    при первом запуске стенда. Объекты, события, погода, прогоны конвейера,
    замеры моделей и учётные записи остаются.
    """
    entries = conn.execute(
        delete(action_log).where(action_log.c.entity_type.in_(("prediction", "order")))
    ).rowcount
    orders = conn.execute(delete(work_order)).rowcount
    predictions = conn.execute(delete(prediction)).rowcount
    # Номера заявок тоже с начала: 2026-0001, как на новом стенде.
    conn.execute(text("ALTER SEQUENCE work_order_number_seq RESTART WITH 1"))
    return ResetResult(predictions=int(predictions), orders=int(orders), log_entries=int(entries))
