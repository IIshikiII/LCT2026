"""Район объекта это код округа его трассы.

Фильтр по районам и роль «диспетчер района» знают коды округов. Загрузчик
раньше писал в район имя комплекса, и фильтр давал пустой журнал.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import engine
from app.ingest.districts import assign, okrug_of
from app.synth.geo import OKRUGS, Okrug, point_on
from app.tables import collector, facility

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.mark.parametrize("okrug", OKRUGS, ids=[item.code for item in OKRUGS])
def test_a_line_on_the_trace_of_an_okrug_belongs_to_it(okrug: Okrug) -> None:
    line = [list(point_on(okrug.line, share)) for share in (0.3, 0.35, 0.4)]
    assert okrug_of(line) == okrug.code


def test_assign_replaces_the_complex_name_with_the_okrug_code() -> None:
    sao = next(item for item in OKRUGS if item.code == "SAO")
    line = [list(point_on(sao.line, share)) for share in (0.5, 0.55, 0.6)]
    with engine().begin() as conn:
        conn.execute(
            collector.insert().values(
                code="4391", label="объект Омикрон", district="объект Омикрон", line=line
            )
        )
        conn.execute(
            facility.insert().values(
                id="S-4391-7",
                collector="4391",
                district="объект Омикрон",
                address="объект Омикрон, участок 7",
                lat=line[1][1],
                lon=line[1][0],
                facility_type="section",
                is_active=True,
            )
        )
        done = assign(conn)
        district = conn.execute(
            select(facility.c.district).where(facility.c.id == "S-4391-7")
        ).scalar_one()

    assert district == "SAO"
    assert done.facilities >= 1


def test_a_second_run_changes_nothing() -> None:
    with engine().begin() as conn:
        assign(conn)
        again = assign(conn)
    assert (again.collectors, again.facilities, again.accounts) == (0, 0, 0)
