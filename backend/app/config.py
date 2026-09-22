"""Конфигурация. Один источник — переменные окружения."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass


def _split(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _auth_secret() -> str:
    """Ключ подписи токенов.

    Переменная не названа значит ключ создаётся случайным на этот запуск. Так
    разработчик поднимает сервер без настройки, а перезапуск снимает все
    сессии. Общий ключ по умолчанию был бы хуже: он одинаков на всех машинах и
    подделать токен с ним может кто угодно.
    """
    named = os.environ.get("AUTH_SECRET", "")
    if named:
        return named
    logging.getLogger("app.auth").warning(
        "переменная AUTH_SECRET не названа, ключ подписи создан на этот запуск: "
        "сессии не переживут перезапуск сервера"
    )
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Config:
    database_url: str
    cors_origins: tuple[str, ...]
    log_level: str
    mlflow_tracking_uri: str
    artifacts_dir: str
    enabled_directions: tuple[str, ...]
    auth_secret: str
    # Срок токена сессии. Смена диспетчера длится 12 часов, но токен живёт
    # меньше: украденный токен отзыву не подлежит, и короткий срок это
    # единственное, что ограничивает его пользу.
    auth_token_ttl_minutes: int
    # Срок промежуточного токена между паролем и кодом. Минуты хватает на
    # то, чтобы достать телефон.
    auth_mfa_ttl_seconds: int
    # Пароль демонстрационных учётных записей. Команда `seed` ставит его всем
    # четырём ролям, чтобы стенд поднимался одной командой.
    seed_password: str
    # Режим тестового стенда. Открывает панель учётных записей на экране входа
    # и три эндпоинта под ней: список наборов, создание, удаление.
    #
    # **Флаг раздаёт логины и пароли без входа в систему.** Он существует ради
    # жюри: каждый проверяющий берёт свой набор и заводит второй фактор в своём
    # телефоне. В промышленной установке он выключен, и это не рекомендация, а
    # условие: включённый флаг делает вход бессмысленным. Разбор в ADR 0007.
    test_stand: bool

    @staticmethod
    def from_environ() -> Config:
        return Config(
            # Адрес 127.0.0.1, а не `localhost`. На Windows имя разрешается
            # сначала в IPv6, Docker публикует порт на `::`, но соединение туда
            # не устанавливается. Клиент ждёт таймаута операционной системы и
            # только потом идёт на IPv4. Это стоило 130 секунд каждому запуску
            # тестов. В образах адрес приходит из переменной и равен `db:5432`.
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://arm:arm@127.0.0.1:5432/arm",
            ),
            cors_origins=_split(os.environ.get("CORS_ORIGINS", "http://localhost:5173")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            mlflow_tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", ""),
            artifacts_dir=os.environ.get("ARTIFACTS_DIR", "artifacts"),
            enabled_directions=_split(os.environ.get("ENABLED_DIRECTIONS", "")),
            auth_secret=_auth_secret(),
            auth_token_ttl_minutes=int(os.environ.get("AUTH_TOKEN_TTL_MINUTES", "480")),
            auth_mfa_ttl_seconds=int(os.environ.get("AUTH_MFA_TTL_SECONDS", "300")),
            seed_password=os.environ.get("SEED_PASSWORD", "collector"),
            test_stand=os.environ.get("TEST_STAND", "").lower() in {"1", "true", "yes"},
        )


config = Config.from_environ()
