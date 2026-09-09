"""Сборка приложения FastAPI."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import logging as app_logging
from app.config import config
from app.db import get_conn

app_logging.setup(config.log_level)

app = FastAPI(
    title="АРМ диспетчера ОДС",
    version="0.1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz(conn: Annotated[Connection, Depends(get_conn)]) -> dict[str, str]:
    conn.execute(text("SELECT 1"))
    return {"status": "ok"}
