"""Плагин доступа без файла модели. Задача T16.

Этот файл не подменяет ни `load_model`, ни `shap`, ни `lightgbm` — в отличие
от `test_plugin_access.py`. Он проверяет настоящий путь: реестр моделей ищет
файл в пустом `ARTIFACTS_DIR`, находит `None`, и `predict`/`explain` обязаны
отказать чистой `RuntimeError` раньше, чем дойдут до импорта LightGBM и SHAP.
`backend/.venv` эти две библиотеки не держит (`docs/08-ml-plugin.md`, T29), и
если бы плагин добрался до `import lightgbm` или `import shap`, тест упал бы
на `ModuleNotFoundError`, а не на ожидаемой `RuntimeError`.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.config import config as real_config
from app.ml import tracking
from app.ml.plugins.unauthorized_access import UnauthorizedAccess

FEATURES: dict[str, float] = {"n_alarms_24h": 3.0, "hours_since_last_alarm": 2.0}


@pytest.fixture
def empty_artifacts_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Реестр MLflow выключен, каталог с моделями пуст — как в чистом деплое."""
    fake = dataclasses.replace(real_config, mlflow_tracking_uri="", artifacts_dir=str(tmp_path))
    monkeypatch.setattr(tracking, "config", fake)
    return tmp_path


def test_predict_without_a_model_file_raises_cleanly(empty_artifacts_dir: Path) -> None:
    with pytest.raises(RuntimeError, match="модель"):
        UnauthorizedAccess().predict(FEATURES)


def test_explain_without_a_model_file_raises_cleanly(empty_artifacts_dir: Path) -> None:
    with pytest.raises(RuntimeError, match="модель"):
        UnauthorizedAccess().explain(FEATURES)


def test_missing_model_does_not_surface_as_module_not_found_error(
    empty_artifacts_dir: Path,
) -> None:
    """Отказ обязан быть `RuntimeError` о модели, а не `ModuleNotFoundError`.

    Проверяет ровно то, что отличает T16 от готового T13: плагин обязан
    проверить наличие модели раньше, чем дойдёт до `import lightgbm` или
    `import shap`, иначе пустой каталог артефактов ронял бы прогон с
    непонятной для диспетчера ошибкой библиотеки.
    """
    try:
        UnauthorizedAccess().predict(FEATURES)
    except Exception as error:
        assert isinstance(error, RuntimeError)
        assert not isinstance(error, ModuleNotFoundError)
    else:
        pytest.fail("predict() обязан отказать без модели")
