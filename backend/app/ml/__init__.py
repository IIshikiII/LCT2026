"""Модели направлений: протокол плагина, реестр предикторов, реестр версий.

Набор зависимостей `ml`. Образ `api` этот пакет не содержит, поэтому ни один
модуль под `app/api/` не имеет права его импортировать.
"""

from __future__ import annotations

from app.ml.protocol import Block, FeatureContext, FeatureVector, Predictor, Window
from app.ml.registry import active, get, missing, register
from app.ml.tracking import load_model, log_run

__all__ = [
    "Block",
    "FeatureContext",
    "FeatureVector",
    "Predictor",
    "Window",
    "active",
    "get",
    "load_model",
    "log_run",
    "missing",
    "register",
]
