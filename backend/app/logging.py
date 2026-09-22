"""JSON-логи поверх stdlib logging. Внешней библиотеки логирования нет."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.request_context import get_request_id

_SKIP = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}

# Имена, которые занимает сама запись. Поле из `extra` с таким именем не имеет
# права их заменить: строка `"level": "CRITICAL"` про уровень риска прогноза
# читалась как серьёзность записи, и семь обычных заявок выглядели в логе
# авариями службы. Совпавшее имя получает приставку, а не исчезает молча.
_RESERVED = frozenset({"ts", "level", "logger", "message", "requestId", "exc"})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": get_request_id(),
        }
        for key, value in record.__dict__.items():
            if key in _SKIP:
                continue
            payload["extra_" + key if key in _RESERVED else key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
