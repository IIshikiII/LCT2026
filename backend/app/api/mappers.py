"""Строка базы -> DTO ответа."""

from __future__ import annotations

from typing import Any

from app.api.common import iso
from app.schemas import (
    CardBlock,
    FacilityRef,
    Prediction,
    PredictionDetail,
    WorkOrder,
    WorkOrderOutcome,
)


def facility_ref(row: Any) -> FacilityRef:
    return FacilityRef(
        id=row.id,
        collector=row.collector,
        section=row.section,
        chamber=row.chamber,
        device=row.device,
        district=row.district,
        address=row.address,
        lat=row.lat,
        lon=row.lon,
    )


def prediction(
    row: Any,
    facility: FacilityRef,
    order_id: str | None,
    facility_order_id: str | None = None,
) -> Prediction:
    # Итог бригады выводится из терминального статуса, а не из отдельного поля:
    # заявка закрывает прогноз своим исходом, и второго источника нет. ADR 0006.
    fact_confirmed: bool | None = None
    if row.status == "CLOSED_CONFIRMED":
        fact_confirmed = True
    elif row.status == "CLOSED_NOT_CONFIRMED":
        fact_confirmed = False

    return Prediction(
        id=row.id,
        direction=row.direction,
        level=row.level,
        probability=row.probability,
        horizon_hours=row.horizon_hours,
        computed_at=iso(row.computed_at),
        compute_ms=row.compute_ms,
        status=row.status,
        facility=facility,
        summary=row.summary,
        order_id=order_id,
        facility_order_id=facility_order_id,
        assignee=getattr(row, "assignee", None),
        verdict=getattr(row, "verdict", None),
        dispatcher_level=getattr(row, "dispatcher_level", None),
        decided_at=iso(row.decided_at) if getattr(row, "decided_at", None) else None,
        fact_confirmed=fact_confirmed,
    )


def blocks_of(raw: Any) -> list[CardBlock]:
    """Разбирает JSONB `blocks`.

    Битый блок пропускается, а не роняет карточку. Направление
    `UNAUTHORIZED_ACCESS` отдаёт мусор намеренно, спецификация §12.
    """
    if not isinstance(raw, list):
        return []
    result: list[CardBlock] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result.append(
            CardBlock(
                type=str(item.get("type", "unknown")),
                title=str(item.get("title", "")),
                data=item.get("data"),
            )
        )
    return result


def prediction_detail(base: Prediction, raw_blocks: Any, actions: list[Any]) -> PredictionDetail:
    return PredictionDetail(
        **base.model_dump(),
        blocks=blocks_of(raw_blocks),
        actions=actions,
    )


def outcome_of(raw: Any) -> WorkOrderOutcome | None:
    """Разбирает JSONB `outcome`. Неполный итог пропускается, а не роняет ответ."""
    if not isinstance(raw, dict):
        return None
    parsed = WorkOrderOutcome.model_validate(raw)
    return parsed


def work_order(row: Any, facility: FacilityRef, actions: list[Any]) -> WorkOrder:
    return WorkOrder(
        id=row.id,
        number=row.number,
        prediction_id=row.prediction_id,
        facility=facility,
        work_type=row.work_type,
        due_at=iso(row.due_at),
        status=row.status,
        created_by=getattr(row, "created_by", "PIPELINE"),
        created_at=iso(row.created_at),
        actions=actions,
        outcome=outcome_of(row.outcome),
    )
