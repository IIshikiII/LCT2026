"""Токен сессии: JWT с подписью HS256.

Формат стандартный, поэтому токен читает любой прокси и любая отладочная
утилита. Подпись симметричная: проверяет её тот же процесс, который выдал
токен, и второй стороны у нас нет.

Библиотеки JWT здесь нет по той же причине, что и у пароля: подпись это
`hmac.new(secret, f"{header}.{payload}", sha256)`, и ради неё зависимость
работающего процесса не заводится (спецификация §2).

**Чего эта реализация не делает.** Она принимает только `HS256` и не смотрит
на поле `alg` из присланного токена. Это защита от подмены алгоритма: токен с
`alg: none` или `alg: RS256` не пройдёт проверку, потому что проверка одна.

Токен не отзывается. Выход из системы снимает токен на стороне браузера, а
сервер ждёт его истечения. Держать список отозванных токенов значило бы
завести состояние, а срок жизни и так короткий.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

ALGORITHM = "HS256"
HEADER = {"alg": ALGORITHM, "typ": "JWT"}

# Вид токена. Первый шаг входа выдаёт токен `mfa`, и он не открывает ни одного
# эндпоинта данных. Без разделения код второго фактора можно было бы обойти,
# предъявив промежуточный токен вместо итогового.
KIND_ACCESS = "access"
KIND_MFA = "mfa"


class TokenError(Exception):
    """Токен не прошёл проверку. Текст идёт в журнал, а не пользователю."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(secret: str, message: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest())


def sign(payload: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    """Собирает токен. Поля `iat` и `exp` ставит сам."""
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl_seconds}
    header = _b64(json.dumps(HEADER, separators=(",", ":")).encode("utf-8"))
    claims = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    message = f"{header}.{claims}"
    return f"{message}.{_signature(secret, message)}"


def verify(token: str, secret: str, kind: str) -> dict[str, Any]:
    """Проверяет подпись, срок и вид токена. Отдаёт полезную нагрузку.

    Порядок проверок важен: подпись первая. Разбирать содержимое неподписанного
    токена значит доверять тому, что прислал клиент.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("токен не состоит из трёх частей")
    header, claims, signature = parts
    if not hmac.compare_digest(_signature(secret, f"{header}.{claims}"), signature):
        raise TokenError("подпись не совпала")

    try:
        payload = json.loads(_unb64(claims))
    except (ValueError, TypeError) as error:
        raise TokenError("полезная нагрузка не разбирается") from error
    if not isinstance(payload, dict):
        raise TokenError("полезная нагрузка не объект")

    if int(payload.get("exp", 0)) <= int(time.time()):
        raise TokenError("срок токена истёк")
    if payload.get("kind") != kind:
        raise TokenError(f"вид токена {payload.get('kind')!r}, ожидался {kind!r}")
    return payload
