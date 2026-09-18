"""Конфигурация. Один источник — переменные окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _split(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    database_url: str
    cors_origins: tuple[str, ...]
    log_level: str
    mlflow_tracking_uri: str
    artifacts_dir: str
    enabled_directions: tuple[str, ...]

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
        )


config = Config.from_environ()
