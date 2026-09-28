"""Уведомления о критических инцидентах. ТЗ §10, ADR 0019.

Уведомление живёт, пока есть прогноз критического уровня, который никто не
взял в работу: он в статусе «Новый». Действие «Взять в работу» переводит
прогноз в работу, и как только таких прогнозов не остаётся, уведомление
пропадает само.

Что считать тревогой, решает сервер. Фронт не знает кодов уровней и статусов:
он получает число, готовый текст, фильтр журнала для ссылки и шаг повтора.
Граница роли та же, что у журнала: диспетчер района слышит только свои
инциденты.

Уведомление зовёт взять инцидент в работу, поэтому слышит его только роль с
правом `take`: диспетчер ОДС и диспетчер района. Технику и группе
реагирования звать некого, у них уведомлений нет.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.api.predictions import JOINED
from app.auth import scope
from app.auth.deps import CurrentActor
from app.db import get_conn
from app.schemas import Alert, AlertFilter
from app.tables import prediction

router = APIRouter(tags=["alerts"])

# Правило тревоги. Уровень и статус это коды реестра `app/meta/catalog.py`.
ALERT_CODE = "critical_untaken"
ALERT_LEVELS = ("CRITICAL",)
ALERT_STATUSES = ("NEW",)
REPEAT_MINUTES = 5
# Право, которое делает роль адресатом уведомления.
ALERT_ACTION = "take"


def _plural(n: int) -> str:
    """«1 критический инцидент не взят», «3 критических инцидента не взяты»."""
    tail = n % 100
    if n % 10 == 1 and tail != 11:
        return f"{n} критический инцидент не взят в работу"
    if n % 10 in (2, 3, 4) and tail not in (12, 13, 14):
        return f"{n} критических инцидента не взяты в работу"
    return f"{n} критических инцидентов не взяты в работу"


@router.get("/alerts", response_model=list[Alert], response_model_by_alias=True)
def alerts(conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor) -> list[Alert]:
    """Действующие уведомления. Пустой список значит тревоги нет."""
    if not actor.may(ALERT_ACTION):
        return []
    statement = scope.apply_joined(
        select(func.count(), func.max(prediction.c.computed_at)).select_from(JOINED), actor
    ).where(
        prediction.c.level.in_(ALERT_LEVELS),
        prediction.c.status.in_(ALERT_STATUSES),
    )
    count, newest = conn.execute(statement).one()
    if not count:
        return []
    return [
        Alert(
            code=ALERT_CODE,
            level=ALERT_LEVELS[0],
            count=int(count),
            title=_plural(int(count)),
            hint="Возьмите инциденты в работу в журнале прогнозов",
            filter=AlertFilter(level=list(ALERT_LEVELS), status=list(ALERT_STATUSES)),
            repeat_minutes=REPEAT_MINUTES,
            newest_at=newest.isoformat() if newest else None,
        )
    ]
