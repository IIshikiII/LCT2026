"""Округ объекта для фильтра по районам и области видимости ролей.

Координат в выгрузке нет. Загрузчик кладёт каждый комплекс на трассу одного
из округов Москвы (`app/synth/geo.py`), чтобы показать карту и фильтр по
районам. Район объекта это код этого округа: его знает справочник районов
`/meta`, по нему режет выборку роль «диспетчер района».

Модуль чинит базу, загруженную до этого правила: там в районе лежало имя
комплекса, и фильтр по району давал пустой журнал. Округ восстанавливается по
трассе объекта на карте, повторный запуск ничего не меняет.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from app.auth.seed import _first_collector, scope_for
from app.synth.geo import OKRUGS, point_on
from app.tables import app_user, collector, facility

# Сколько точек брать на трассе округа при поиске ближайшей.
SAMPLES = 200


@dataclass(frozen=True)
class Assigned:
    collectors: int
    facilities: int
    accounts: int


def _samples() -> list[tuple[str, tuple[float, float]]]:
    return [
        (okrug.code, point_on(okrug.line, index / SAMPLES))
        for okrug in OKRUGS
        for index in range(SAMPLES + 1)
    ]


def okrug_of(line: list[Any]) -> str | None:
    """Код округа, на трассе которого лежит ломаная объекта."""
    if not line:
        return None
    lon, lat = line[len(line) // 2]
    samples = _samples()
    code, _ = min(
        samples,
        key=lambda item: (item[1][0] - float(lon)) ** 2 + (item[1][1] - float(lat)) ** 2,
    )
    return code


def assign(conn: Connection) -> Assigned:
    """Проставляет объектам код округа и обновляет области тестовых учёток."""
    collectors = 0
    facilities = 0
    for row in conn.execute(select(collector.c.code, collector.c.line, collector.c.district)):
        code = okrug_of(list(row.line or []))
        if code is None:
            continue
        if row.district != code:
            conn.execute(
                update(collector).where(collector.c.code == row.code).values(district=code)
            )
            collectors += 1
        facilities += conn.execute(
            update(facility)
            .where(facility.c.collector == row.code, facility.c.district.is_distinct_from(code))
            .values(district=code)
        ).rowcount

    # Тестовые учётки узких ролей смотрят на первый коллектор и его район.
    # Набор заводили до загрузки выгрузки, и границы указывали в пустоту.
    complex_code, district = _first_collector(conn)
    accounts = 0
    for user in conn.execute(
        select(app_user.c.username, app_user.c.role, app_user.c.scope_value).where(
            app_user.c.demo_set.isnot(None)
        )
    ):
        value = scope_for(user.role, complex_code, district)
        if value != user.scope_value:
            conn.execute(
                update(app_user)
                .where(app_user.c.username == user.username)
                .values(scope_value=value)
            )
            accounts += 1
    return Assigned(collectors=collectors, facilities=int(facilities), accounts=accounts)
