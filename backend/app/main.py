"""Сборка приложения FastAPI."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import errors, middleware
from app import logging as app_logging
from app.api import ROUTERS
from app.config import config
from app.db import get_conn

API_PREFIX = "/api/v1"

app_logging.setup(config.log_level)

app = FastAPI(
    title="АРМ диспетчера ОДС",
    version="0.1.0",
    docs_url=f"{API_PREFIX}/docs",
    openapi_url=f"{API_PREFIX}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

middleware.install(app)
errors.install(app)

for router in ROUTERS:
    app.include_router(router, prefix=API_PREFIX)


@app.get("/healthz")
def healthz(conn: Annotated[Connection, Depends(get_conn)]) -> dict[str, str]:
    conn.execute(text("SELECT 1"))
    return {"status": "ok"}
