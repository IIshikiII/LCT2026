"""Перенос замера модели в базу. Спецификация §9, §10.

Виджет «Соответствие метрикам ТЗ» стоял пустым: обучение писало числа в файл,
а сервис читал пустую таблицу. Тест держит соединение этих двух концов.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import engine
from app.ml import publish as publish_module
from app.tables import model_metric

DIRECTION = "UNAUTHORIZED_ACCESS"

MEASURE: dict[str, Any] = {
    "decision": {
        "threshold": 0.156089,
        "chosen": {"cell_precision": 0.193738, "cell_recall": 0.12289},
    },
    "model_version": "latest",
}


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Подменяет каталог моделей на временный.

    `Config` заморожен, поэтому подменяется весь объект целиком, а не поле.
    """
    monkeypatch.setattr(
        publish_module,
        "config",
        dataclasses.replace(publish_module.config, artifacts_dir=str(tmp_path)),
    )
    return tmp_path


def _write(artifacts: Path, payload: dict[str, Any]) -> None:
    folder = artifacts / DIRECTION.lower()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / publish_module.METRICS_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_a_missing_measure_is_not_an_error(artifacts: Path, seeded: None) -> None:
    """Направление без обученной модели это законное состояние."""
    with engine().begin() as conn:
        assert publish_module.publish(conn, DIRECTION) is False


def test_a_broken_measure_is_not_an_error(artifacts: Path, seeded: None) -> None:
    folder = artifacts / DIRECTION.lower()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / publish_module.METRICS_FILENAME).write_text("{не json", encoding="utf-8")

    with engine().begin() as conn:
        assert publish_module.publish(conn, DIRECTION) is False


def test_a_measure_without_the_working_point_is_refused(artifacts: Path, seeded: None) -> None:
    _write(artifacts, {"decision": {"threshold": 0.1}})

    with engine().begin() as conn:
        assert publish_module.publish(conn, DIRECTION) is False


def test_the_published_numbers_come_from_the_working_threshold(
    artifacts: Path, seeded: None
) -> None:
    """Метрика описывает тот порог, который стоит в работе.

    Лучшая точка кривой описывает режим, в котором сервис не работает, и на
    дашборде она вводила бы диспетчера в заблуждение.
    """
    _write(artifacts, MEASURE)

    with engine().begin() as conn:
        assert publish_module.publish(conn, DIRECTION) is True

    with engine().connect() as conn:
        row = conn.execute(
            select(model_metric).where(model_metric.c.direction == DIRECTION)
        ).one()

    assert row.precision_value == pytest.approx(0.193738)
    assert row.recall_value == pytest.approx(0.12289)
    assert row.method == publish_module.METHOD
    assert row.model_version == "latest"
    # Умолчания ТЗ §9. Порогом приёмки они не являются.
    assert row.target_precision == pytest.approx(0.7)
    assert row.target_recall == pytest.approx(0.5)


def test_publishing_twice_keeps_one_row(artifacts: Path, seeded: None) -> None:
    """История замеров живёт в MLflow. В базе лежит текущий."""
    _write(artifacts, MEASURE)

    with engine().begin() as conn:
        publish_module.publish(conn, DIRECTION)
        publish_module.publish(conn, DIRECTION)

    with engine().connect() as conn:
        rows = list(
            conn.execute(select(model_metric).where(model_metric.c.direction == DIRECTION))
        )

    assert len(rows) == 1
