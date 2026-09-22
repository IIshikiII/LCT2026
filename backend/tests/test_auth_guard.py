"""Проверка, что защищённых маршрутов без токена не осталось.

Тест обходит маршруты роутеров и смотрит, стоит ли на каждом зависимость
`current_actor`. Так забытый маршрут находится сразу, а не после того, как он
отдаст данные кому не надо.

Маршруты берутся из `app.api.ROUTERS`, а не из собранного приложения. Причина
простая: `FastAPI.include_router` заворачивает роутер во внутренний объект, и
разбор этого объекта привязал бы тест к версии библиотеки.

Список открытых маршрутов лежит в `app/api/__init__.py` и правится осознанно.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.api import PUBLIC_PATHS, ROUTERS
from app.auth.deps import current_actor
from app.main import API_PREFIX, app


def _routes() -> list[tuple[str, APIRoute]]:
    """Пары «полный путь и маршрут». Маршруты приложения плюс маршруты API."""
    found = [(route.path, route) for route in app.routes if isinstance(route, APIRoute)]
    for router in ROUTERS:
        found += [
            (f"{API_PREFIX}{route.path}", route)
            for route in router.routes
            if isinstance(route, APIRoute)
        ]
    return found


def _guarded(route: APIRoute) -> bool:
    return any(item.call is current_actor for item in route.dependant.dependencies)


def test_every_route_is_guarded_or_listed_as_public() -> None:
    unguarded = sorted(
        path for path, route in _routes() if not _guarded(route) and path not in PUBLIC_PATHS
    )
    assert unguarded == []


def test_the_open_list_stays_short() -> None:
    """Открытый маршрут добавляется осознанно, поэтому список записан здесь.

    Два последних пути раздают логины тестового стенда, и они открыты
    намеренно: панель рисуется на экране входа, где токена ещё нет.
    Наполняются они только при флаге `TEST_STAND`, иначе отдают пустой
    список. ADR 0007.
    """
    assert (
        frozenset(
            {
                "/healthz",
                "/api/v1/auth/login",
                "/api/v1/auth/mfa",
                "/api/v1/docs",
                "/api/v1/docs/oauth2-redirect",
                "/api/v1/openapi.json",
                "/api/v1/auth/test-accounts",
                "/api/v1/auth/test-accounts/{demo_set}",
            }
        )
        == PUBLIC_PATHS
    )


def test_the_check_itself_sees_routes() -> None:
    """Пустой обход прошёл бы молча и ничего не проверил."""
    paths = [path for path, _ in _routes()]
    assert len(paths) > 10
    assert "/api/v1/predictions" in paths


def test_a_data_route_is_recognised_as_guarded() -> None:
    """Проверка обязана отличать защищённый маршрут от открытого."""
    routes = dict(_routes())
    assert _guarded(routes["/api/v1/predictions"])
    assert not _guarded(routes["/api/v1/auth/login"])
