"""Реестр версий моделей.

Главное требование спецификации §2: недоступный MLflow не останавливает ни
обучение, ни прогноз. Поэтому все тесты идут с выключенным трекингом.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.config import config as real_config
from app.ml import tracking


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Выключает трекинг и уводит хранилище моделей во временный каталог."""
    fake = dataclasses.replace(real_config, mlflow_tracking_uri="", artifacts_dir=str(tmp_path))
    monkeypatch.setattr(tracking, "config", fake)
    return tmp_path


def test_log_run_without_a_server_does_not_raise(offline: Path) -> None:
    assert tracking.log_run("SENSOR_FAILURE", {"depth": 6}, {"precision": 0.81}) is None


def test_log_run_saves_the_model_to_a_file(offline: Path) -> None:
    """Файл — запасной путь прогноза, поэтому он пишется всегда."""
    tracking.log_run("SENSOR_FAILURE", {}, {}, model={"kind": "fake"}, version="7")

    assert (offline / "sensor_failure" / "7.joblib").is_file()


def test_load_model_returns_none_when_nothing_is_trained(offline: Path) -> None:
    assert tracking.load_model("WEAR_OUT") is None


def test_the_model_survives_a_round_trip_through_the_file(offline: Path) -> None:
    tracking.log_run("FIRE_RISK", {}, {}, model={"kind": "fake"}, version="3")

    assert tracking.load_model("FIRE_RISK") == {"kind": "fake"}
    assert tracking.load_model("FIRE_RISK", version="3") == {"kind": "fake"}


def test_load_model_takes_the_highest_version(offline: Path) -> None:
    """Выбор не опирается на время правки: две записи одной секунды равны."""
    tracking.log_run("FIRE_RISK", {}, {}, model={"version": 1}, version="1")
    tracking.log_run("FIRE_RISK", {}, {}, model={"version": 2}, version="2")

    assert tracking.load_model("FIRE_RISK") == {"version": 2}


def test_a_run_without_a_version_wins_over_numbered_files(offline: Path) -> None:
    """Обучение без номера пишет файл `latest` и перезаписывает его."""
    tracking.log_run("FIRE_RISK", {}, {}, model={"version": 1}, version="1")
    tracking.log_run("FIRE_RISK", {}, {}, model={"version": "latest"})

    assert tracking.load_model("FIRE_RISK") == {"version": "latest"}


def test_an_unknown_version_gives_none(offline: Path) -> None:
    tracking.log_run("FIRE_RISK", {}, {}, model={"version": 1}, version="1")

    assert tracking.load_model("FIRE_RISK", version="99") is None


def test_a_broken_file_does_not_stop_the_run(offline: Path) -> None:
    """Битый файл модели лечится обучением, а не падением конвейера."""
    folder = offline / "fire_risk"
    folder.mkdir(parents=True)
    (folder / "1.joblib").write_bytes(b"not a model")

    assert tracking.load_model("FIRE_RISK") is None
