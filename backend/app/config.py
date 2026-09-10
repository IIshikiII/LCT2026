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

    @staticmethod
    def from_environ() -> Config:
        return Config(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://arm:arm@localhost:5432/arm",
            ),
            cors_origins=_split(os.environ.get("CORS_ORIGINS", "http://localhost:5173")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            mlflow_tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", ""),
        )


config = Config.from_environ()
