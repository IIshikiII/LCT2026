"""Формат JSON-логов. Спецификация §11.

Журнал читает дежурный и система сбора логов. Поэтому имя поля `level` обязано
означать серьёзность записи всегда, а не иногда.
"""

from __future__ import annotations

import json
import logging

from app.logging import JsonFormatter


def _format(**extra: object) -> dict[str, object]:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="сообщение",
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def test_the_record_carries_the_fixed_fields() -> None:
    payload = _format()

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "сообщение"
    assert "ts" in payload


def test_an_extra_field_reaches_the_record() -> None:
    payload = _format(orderId="O-1", direction="UNAUTHORIZED_ACCESS")

    assert payload["orderId"] == "O-1"
    assert payload["direction"] == "UNAUTHORIZED_ACCESS"


def test_an_extra_field_does_not_replace_the_severity() -> None:
    """Автозаявка писала уровень риска в поле `level`.

    Поле затирало серьёзность записи, и семь обычных заявок выглядели в логе
    семью авариями службы. Значение сохраняется, но под другим именем.
    """
    payload = _format(level="CRITICAL")

    assert payload["level"] == "INFO"
    assert payload["extra_level"] == "CRITICAL"


def test_no_reserved_field_can_be_replaced() -> None:
    """Имена `message` и `asctime` в списке не нужны: сам `logging` не даёт
    передать их через `extra` и поднимает `KeyError`. Проверяются те имена,
    которые до форматтера доходят."""
    payload = _format(ts="подделка", logger="чужой", requestId="X", exc="выдумка")

    assert payload["ts"] != "подделка"
    assert payload["logger"] == "app.test"
    assert payload["requestId"] != "X"
    assert payload["extra_ts"] == "подделка"
    assert payload["extra_logger"] == "чужой"
    assert payload["extra_requestId"] == "X"
    assert payload["extra_exc"] == "выдумка"
