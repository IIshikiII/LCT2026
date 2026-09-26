"""Роутеры API. По одному на область экрана.

Все монтируются под `/api/v1`. Добавить область — значит добавить модуль и
строку в `ROUTERS`.

**Открытые маршруты перечислены здесь.** Всё остальное требует токен. Список
короткий по смыслу: войти можно только тем, у кого токена ещё нет, а проверка
здоровья зовётся системой развёртывания до всякого входа. Тест
`tests/test_auth_guard.py` обходит маршруты приложения и падает, если
защищённый маршрут остался без зависимости `current_actor`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    alerts,
    auth,
    dashboard,
    facilities,
    meta,
    metrics,
    orders,
    predictions,
    stream,
    test_stand,
)

ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    test_stand.router,
    meta.router,
    predictions.router,
    facilities.router,
    orders.router,
    metrics.router,
    dashboard.router,
    alerts.router,
    stream.router,
)

# Полные пути, которым токен не нужен. Сверяются с `route.path` приложения.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/healthz",
        "/api/v1/auth/login",
        "/api/v1/auth/mfa",
        # Описание API и страница Swagger. Данных они не отдают, а жюри и
        # интегратор открывают их до всякого входа.
        "/api/v1/docs",
        "/api/v1/docs/oauth2-redirect",
        "/api/v1/openapi.json",
        # Панель тестового стенда. Она рисуется на экране входа, и человек без
        # токена обязан увидеть, чем войти. Наполняется только при флаге
        # `TEST_STAND`, иначе отдаёт пустой список. ADR 0007.
        "/api/v1/auth/test-accounts",
        "/api/v1/auth/test-accounts/{demo_set}",
        # Поток СМВУ. Его шлёт система, а не человек, и ручка проверяет свой
        # ключ `STREAM_TOKEN`. Без ключа ручка отвечает 404. ADR 0017.
        "/api/v1/stream/events",
    }
)

__all__ = ["PUBLIC_PATHS", "ROUTERS"]
