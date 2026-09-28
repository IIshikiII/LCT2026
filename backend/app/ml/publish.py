"""Перенос замера модели в базу. Спецификация §9, §10.

Дашборд показывает виджет «Здоровье модели» из таблицы
`model_metric`. Таблицу никто не наполнял: обучение писало числа в
`ml/access/out/metrics.json`, а сервис читал пустую таблицу и рисовал пустой
виджет. Этот модуль соединяет два конца.

Файл замера берётся из каталога модели, `ARTIFACTS_DIR/<направление>/`, потому
что замер обязан ехать вместе с моделью. Модель без своего замера означает
число на дашборде от другой версии, а это хуже, чем пустой виджет.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.engine import Connection

from app.config import config
from app.tables import model_metric

log = logging.getLogger(__name__)

METRICS_FILENAME = "metrics.json"

# Способ получения числа. Поле обязано честно называть способ, потому что
# офлайн-оценка и отметки диспетчера не сравнимы между собой.
METHOD = "offline_holdout"


def metrics_path(direction: str) -> Path:
    return Path(config.artifacts_dir) / direction.lower() / METRICS_FILENAME


def read_measure(direction: str) -> dict[str, Any] | None:
    """Читает замер направления. Отдаёт None, когда файла нет или он битый."""
    path = metrics_path(direction)
    if not path.exists():
        log.info("замер модели не найден", extra={"direction": direction, "path": str(path)})
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning(
            "замер модели не прочитан",
            extra={"direction": direction, "path": str(path), "error": str(error)},
        )
        return None
    return payload


def _point(payload: dict[str, Any]) -> tuple[float, float] | None:
    """Точность и полнота на том пороге, который стоит в работе.

    Берётся `decision.chosen`, а не лучшая точка кривой. Диспетчер видит те
    прогнозы, которые прошли рабочий порог, и метрика обязана описывать их, а
    не режим, в котором сервис не работает.
    """
    chosen = payload.get("decision", {}).get("chosen")
    if not isinstance(chosen, dict):
        return None
    precision = chosen.get("cell_precision")
    recall = chosen.get("cell_recall")
    if precision is None or recall is None:
        return None
    return float(precision), float(recall)


def _baseline(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """Точность и полнота наивного правила «событие было вчера».

    Доступ кладёт правило в `decision.naive`, подтопление в `test.naive`.
    Замер без правила отдаёт пустые значения, и дашборд пишет прочерк.
    """
    for naive in (payload.get("decision", {}).get("naive"), payload.get("test", {}).get("naive")):
        if isinstance(naive, dict) and "precision" in naive and "recall" in naive:
            return float(naive["precision"]), float(naive["recall"])
    return None, None


def publish(conn: Connection, direction: str) -> bool:
    """Кладёт замер направления в `model_metric`. Отдаёт True, когда положил.

    Строка направления одна: API берёт последнюю по `evaluated_at`, а история
    замеров живёт в MLflow. Две строки об одной версии только путали бы.
    """
    payload = read_measure(direction)
    if payload is None:
        return False

    point = _point(payload)
    if point is None:
        log.warning("в замере нет рабочей точки", extra={"direction": direction})
        return False

    precision, recall = point
    baseline_precision, baseline_recall = _baseline(payload)
    targets = payload.get("targets", {})

    conn.execute(delete(model_metric).where(model_metric.c.direction == direction))
    conn.execute(
        model_metric.insert(),
        {
            "direction": direction,
            "precision_value": precision,
            "recall_value": recall,
            # Умолчания ТЗ §9. Порогом приёмки они не являются: цель выводится
            # из качества данных. Дашборд показывает их как ориентир.
            "target_precision": float(targets.get("precision", 0.7)),
            "target_recall": float(targets.get("recall", 0.5)),
            "evaluated_at": datetime.now(UTC),
            "method": METHOD,
            "model_version": payload.get("model_version"),
            "baseline_precision": baseline_precision,
            "baseline_recall": baseline_recall,
        },
    )
    log.info(
        "замер модели опубликован",
        extra={"direction": direction, "precision": precision, "recall": recall},
    )
    return True


def publish_all(conn: Connection, directions: tuple[str, ...]) -> list[str]:
    """Публикует замеры всех названных направлений. Отдаёт то, что опубликовал."""
    return [code for code in directions if publish(conn, code)]
