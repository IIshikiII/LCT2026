"""Роутер карты: точки объектов, трассы коллекторов, один объект."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, select
from sqlalchemy.engine import Connection

from app.api import common, mappers
from app.db import get_conn
from app.schemas import FacilityRef
from app.tables import collector, facility, prediction

router = APIRouter(tags=["facilities"])

# Карта показывает по одной точке на объект. Когда прогнозов несколько, берётся
# самый вероятный: DISTINCT ON по объекту с сортировкой по убыванию.
POINTS = (
    select(
        facility.c.id.label("facility_id"),
        facility.c.lon,
        facility.c.lat,
        facility.c.address,
        facility.c.collector,
        prediction.c.id.label("prediction_id"),
        prediction.c.direction,
        prediction.c.level,
        prediction.c.probability,
    )
    .join(prediction, prediction.c.facility_id == facility.c.id)
    .distinct(facility.c.id)
    .order_by(facility.c.id, prediction.c.probability.desc())
)


def _filtered(
    statement: Select[Any],
    directions: list[str],
    levels: list[str],
    district: str | None,
    bbox: common.BBox | None,
) -> Select[Any]:
    if directions:
        statement = statement.where(prediction.c.direction.in_(directions))
    if levels:
        statement = statement.where(prediction.c.level.in_(levels))
    if district:
        statement = statement.where(facility.c.district == district)
    if bbox:
        # Границы включительно: точка на краю экрана обязана попасть в ответ.
        statement = statement.where(
            facility.c.lon.between(bbox.min_lon, bbox.max_lon),
            facility.c.lat.between(bbox.min_lat, bbox.max_lat),
        )
    return statement


@router.get("/facilities")
def list_facilities(
    conn: Annotated[Connection, Depends(get_conn)],
    direction: Annotated[list[str], Query(default_factory=list)],
    level: Annotated[list[str], Query(default_factory=list)],
    district: str | None = None,
    bbox: str | None = None,
) -> dict[str, Any]:
    """Точки объектов в GeoJSON.

    Экран карты не принимает ни статус, ни диапазон дат. Карта показывает риск,
    а не стадию работы по нему.
    """
    rows = conn.execute(
        _filtered(POINTS, direction, level, district, common.parse_bbox(bbox))
    ).all()

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                # Порядок [lon, lat], как требует GeoJSON.
                "geometry": {"type": "Point", "coordinates": [row.lon, row.lat]},
                "properties": {
                    "facilityId": row.facility_id,
                    "predictionId": row.prediction_id,
                    "direction": row.direction,
                    "level": row.level,
                    "probability": row.probability,
                    "address": row.address,
                    "collector": row.collector,
                },
            }
            for row in rows
        ],
    }


@router.get("/facilities/lines")
def list_lines(conn: Annotated[Connection, Depends(get_conn)]) -> dict[str, Any]:
    """Трассы коллекторов. Отдельный эндпоинт: фронт берёт их раз за сессию."""
    rows = conn.execute(select(collector)).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": row.line},
                "properties": {"collector": row.label, "code": row.code},
            }
            for row in rows
        ],
    }


@router.get("/facilities/{facility_id}", response_model=FacilityRef, response_model_by_alias=True)
def get_facility(facility_id: str, conn: Annotated[Connection, Depends(get_conn)]) -> FacilityRef:
    row = conn.execute(select(facility).where(facility.c.id == facility_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"объект {facility_id} не найден")
    return mappers.facility_ref(row)
