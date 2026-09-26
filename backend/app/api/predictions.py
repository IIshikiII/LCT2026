"""Роутер прогнозов: журнал, карточка, таймсерии."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, Select, UnaryExpression, case, func, select
from sqlalchemy.engine import Connection

from app.api import common, mappers
from app.auth import scope
from app.auth.actor import Actor
from app.auth.deps import CurrentActor, check_permission
from app.db import get_conn, get_tx
from app.domain import apply, auto_orders, prediction_actions, transitions
from app.meta import catalog
from app.schemas import Page, Prediction, PredictionDetail, Series, SeriesPoint, TimeSeriesResponse
from app.tables import facility, prediction, sensor_reading, work_order

router = APIRouter(tags=["predictions"])

# Критичность уровня числом из реестра: чем выше, тем опаснее. Уровень это
# строка, и порядок знает только реестр.
SEVERITY: ColumnElement[Any] = case(
    {level.code: level.order for level in catalog.RISK_LEVELS},
    value=prediction.c.level,
    else_=0,
)

SORT_FIELDS = {
    "computedAt": prediction.c.computed_at,
    "probability": prediction.c.probability,
    "risk": SEVERITY,
    "horizon": prediction.c.horizon_hours,
    "direction": prediction.c.direction,
    "status": prediction.c.status,
    "summary": prediction.c.summary,
    "facility": facility.c.address,
}


def ordering(sort: str | None) -> tuple[UnaryExpression[Any], ...]:
    """Порядок журнала. По умолчанию сначала критичность, потом новизна.

    Диспетчер смотрит сверху вниз: критический прогноз выше высокого, а среди
    равных по уровню свежий выше старого. Сортировка по полю из запроса тоже
    добирает равные значения новизной.
    """
    newest = prediction.c.computed_at.desc()
    if not sort:
        return (SEVERITY.desc(), newest)
    return (common.order_by(sort, SORT_FIELDS, SEVERITY.desc()), newest)


# Заявка на прогноз одна. Подзапрос дешевле join с группировкой.
ORDER_ID = (
    select(work_order.c.id)
    .where(work_order.c.prediction_id == prediction.c.id)
    .limit(1)
    .scalar_subquery()
    .label("order_id")
)

JOINED = prediction.join(facility, facility.c.id == prediction.c.facility_id)

BASE = select(prediction, facility, ORDER_ID).select_from(JOINED)


def _filtered(
    statement: Select[Any],
    actor: Actor,
    directions: list[str],
    levels: list[str],
    statuses: list[str],
    district: str | None,
    assignee: str | None,
    date_from: str | None,
    date_to: str | None,
) -> Select[Any]:
    # Граница роли ставится первой и снять её параметром запроса нельзя:
    # фильтры диспетчера сужают выборку внутри того, что ему видно.
    statement = scope.apply_joined(statement, actor)
    if directions:
        statement = statement.where(prediction.c.direction.in_(directions))
    if levels:
        statement = statement.where(prediction.c.level.in_(levels))
    if statuses:
        statement = statement.where(prediction.c.status.in_(statuses))
    if district:
        statement = statement.where(facility.c.district == district)
    if assignee:
        statement = statement.where(prediction.c.assignee == assignee)
    if date_from:
        statement = statement.where(prediction.c.computed_at >= common.day_start(date_from))
    if date_to:
        # Конец дня включительно, как в моках.
        statement = statement.where(prediction.c.computed_at <= common.day_end(date_to))
    return statement


def row_to_prediction(row: Any) -> Prediction:
    return mappers.prediction(row, mappers.facility_ref(row), row.order_id)


@router.get("/predictions", response_model=Page[Prediction], response_model_by_alias=True)
def list_predictions(
    conn: Annotated[Connection, Depends(get_conn)],
    actor: CurrentActor,
    direction: Annotated[list[str], Query(default_factory=list)],
    level: Annotated[list[str], Query(default_factory=list)],
    status: Annotated[list[str], Query(default_factory=list)],
    district: str | None = None,
    assignee: str | None = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    sort: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = common.DEFAULT_PAGE_SIZE,
) -> Page[Prediction]:
    page, page_size = common.clamp_page(page, page_size)
    where = (actor, direction, level, status, district, assignee, from_, to)

    total = conn.execute(_filtered(select(func.count()).select_from(JOINED), *where)).scalar_one()

    rows = conn.execute(
        _filtered(BASE, *where)
        .order_by(*ordering(sort))
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    return Page(
        items=[row_to_prediction(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


def _load(conn: Connection, actor: Actor, prediction_id: str) -> Any:
    """Читает прогноз в границах видимости роли.

    Прогноз вне границы даёт 404, а не 403. Разные коды ответа сказали бы, что
    прогноз с таким номером существует, и техник узнавал бы о работе соседнего
    комплекса по коду ответа.
    """
    row = conn.execute(
        scope.apply_joined(BASE.where(prediction.c.id == prediction_id), actor)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"прогноз {prediction_id} не найден")
    return row


@router.get(
    "/predictions/{prediction_id}",
    response_model=PredictionDetail,
    response_model_by_alias=True,
)
def get_prediction(
    prediction_id: str, conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor
) -> PredictionDetail:
    row = _load(conn, actor, prediction_id)
    return mappers.prediction_detail(
        row_to_prediction(row),
        row.blocks,
        list(prediction_actions(row.status, row.assignee, actor)),
    )


@router.get(
    "/predictions/{prediction_id}/timeseries",
    response_model=TimeSeriesResponse,
    response_model_by_alias=True,
)
def get_timeseries(
    prediction_id: str, conn: Annotated[Connection, Depends(get_conn)], actor: CurrentActor
) -> TimeSeriesResponse:
    """Ряды показаний объекта. Окно выбирает сервер, параметров нет."""
    row = _load(conn, actor, prediction_id)

    readings = conn.execute(
        select(
            sensor_reading.c.metric,
            sensor_reading.c.unit,
            sensor_reading.c.observed_at,
            sensor_reading.c.value,
        )
        .where(sensor_reading.c.facility_id == row.facility_id)
        .order_by(sensor_reading.c.metric, sensor_reading.c.observed_at)
    ).all()

    series: dict[str, Series] = {}
    for reading in readings:
        item = series.get(reading.metric)
        if item is None:
            item = Series(name=reading.metric, unit=reading.unit, points=[])
            series[reading.metric] = item
        item.points.append(SeriesPoint(t=common.iso(reading.observed_at), v=reading.value))

    return TimeSeriesResponse(
        series=list(series.values()),
        marker_at=common.iso(row.computed_at),
    )


@router.post(
    "/predictions/{prediction_id}/actions/{code}",
    response_model=PredictionDetail,
    response_model_by_alias=True,
)
def act_on_prediction(
    prediction_id: str,
    code: str,
    conn: Annotated[Connection, Depends(get_tx)],
    actor: CurrentActor,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> PredictionDetail:
    """Единственный эндпоинт действий над прогнозом.

    Тело — плоский объект значений формы. Ответ — обновлённый прогноз целиком,
    чтобы фронт положил его в кэш без второго запроса.

    Право проверяется здесь, а не только составом кнопок. Список действий
    роли без права и так приходит пустым, но запрос можно послать и мимо
    интерфейса.
    """
    apply.require_known(transitions.PREDICTION, code)
    check_permission(conn, actor, code)
    row = _load(conn, actor, prediction_id)
    status = row.status

    apply.check_body(list(prediction_actions(status, row.assignee, actor)), code, body)
    new_status = apply.next_status(transitions.PREDICTION, code, status)

    extra: dict[str, Any] = {}
    if code == "take":
        extra["assignee"] = actor.username
    elif code == "release":
        extra["assignee"] = None
    elif code == "decide":
        new_status, extra = _decision(row, actor, body)

    apply.set_status(conn, prediction, prediction_id, status, new_status, **extra)
    if code == "decide":
        _settle_order(conn, row, extra["dispatcher_level"])
    apply.log(conn, transitions.PREDICTION, prediction_id, code, actor.username, body)

    updated = _load(conn, actor, prediction_id)
    return mappers.prediction_detail(
        row_to_prediction(updated),
        updated.blocks,
        list(prediction_actions(updated.status, updated.assignee, actor)),
    )


def _decision(row: Any, actor: Actor, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Разбирает решение диспетчера в статус и поля прогноза. ADR 0006.

    Выезд назначает итоговый уровень, а не согласие: диспетчер мог согласиться
    с критическим уровнем или поднять до него средний, и в обоих случаях нужна
    заявка.
    """
    level = str(body.get("dispatcherLevel") or "")
    if not catalog.level_exists(level):
        raise HTTPException(
            status_code=422,
            detail=f"уровень {level!r} не входит в справочник riskLevels",
        )

    verdict = "AGREED" if level == row.level else "CORRECTED"
    extra = {
        "verdict": verdict,
        "dispatcher_level": level,
        "decided_at": datetime.now(UTC),
        "assignee": row.assignee or actor.username,
    }
    if body.get("suppressUntil"):
        extra["suppress_until"] = common.parse_moment(body["suppressUntil"], field="suppressUntil")
    status = "ORDER_OPEN" if catalog.needs_order(level) else "DECIDED"
    return status, extra


def _settle_order(conn: Connection, row: Any, level: str) -> None:
    """Приводит заявку в соответствие с решением диспетчера.

    Четыре случая таблицы ADR 0006 сходятся в два действия: выезд нужен значит
    заявка должна существовать и быть подтверждённой к работе, выезд не нужен
    значит открытая заявка отклоняется.
    """
    order = None
    if row.order_id is not None:
        order = conn.execute(select(work_order).where(work_order.c.id == row.order_id)).first()

    if catalog.needs_order(level):
        if order is None:
            # Диспетчер поднял уровень: заявки не было, её создаёт решение.
            auto_orders.create_for(conn, row, created_by=auto_orders.BY_DISPATCHER)
        elif order.status == "AUTO_CREATED":
            # Заявка ждала решения и дождалась. Отдельного подтверждения нет:
            # диспетчер только что сказал, что выезд нужен.
            apply.set_status(conn, work_order, order.id, order.status, "CONFIRMED")
        return

    if order is not None and order.status in ("AUTO_CREATED", "CONFIRMED"):
        apply.set_status(conn, work_order, order.id, order.status, "REJECTED")
