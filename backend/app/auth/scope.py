"""Область видимости роли. Режет выборку в запросе, а не на экране.

Скрытая колонка и пустая строка в интерфейсе правами не являются: ответ API
уже ушёл в браузер, и посмотреть его может кто угодно. Поэтому граница
ставится условием запроса к базе.

Границу задаёт пара «вид и значение» из учётной записи.

| Вид | Колонка | Кто |
|---|---|---|
| `ALL` | условия нет | диспетчер ОДС, группа реагирования |
| `DISTRICT` | `facility.district` | диспетчер района |
| `COMPLEX` | `facility.collector` | техник |

Слово «комплекс» в ответе заказчика 4.1 означает связку коллекторов, и в нашей
схеме её держит колонка `facility.collector`. Переименование колонки в
`complex_id` идёт вместе с переходом на дерево объектов из выгрузки, и до него
область видимости работает на том, что есть.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, Select, select

from app.auth.actor import Actor
from app.auth.roles import SCOPE_COMPLEX, SCOPE_DISTRICT
from app.tables import facility

# Вид области видимости и колонка объекта, которая его держит.
COLUMNS = {
    SCOPE_DISTRICT: facility.c.district,
    SCOPE_COMPLEX: facility.c.collector,
}


def clause(actor: Actor) -> ColumnElement[bool] | None:
    """Условие по колонкам таблицы `facility`. `None` значит «видно всё».

    Область видимости без значения тоже режет всё: запись «техник без
    комплекса» заведена с ошибкой, и показать ей предприятие целиком хуже, чем
    не показать ничего.
    """
    column = COLUMNS.get(actor.scope_kind)
    if column is None:
        return None
    return column == actor.scope_value


def apply_joined(statement: Select[Any], actor: Actor) -> Select[Any]:
    """Ставит условие на запрос, который уже соединён с `facility`."""
    condition = clause(actor)
    return statement if condition is None else statement.where(condition)


def by_facility_id(column: ColumnElement[Any], actor: Actor) -> ColumnElement[bool] | None:
    """Условие для таблицы без соединения с `facility`.

    Счётчики дашборда считаются по одной таблице, и добавлять соединение ради
    границы значило бы менять план запроса. Подзапрос по первичному ключу
    дешевле.
    """
    condition = clause(actor)
    if condition is None:
        return None
    return column.in_(select(facility.c.id).where(condition))
