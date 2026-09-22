"""Второй фактор: одноразовый код по времени, RFC 6238.

Код считает приложение-аутентификатор на телефоне, сервер считает тот же код
сам и сравнивает. Общий секрет лежит в учётной записи, по сети он проходит
один раз при регистрации ключа.

Реализация занимает сорок строк стандартной библиотеки, поэтому внешней
библиотеки здесь нет. Совместимость полная: тот же HMAC-SHA1, шаг 30 секунд,
шесть цифр и та же ссылка `otpauth://`, что читают Google Authenticator,
Яндекс Ключ и 1Password.

**Окно допуска.** Часы телефона и сервера расходятся, поэтому принимается код
соседнего шага в обе стороны. Окно в один шаг даёт запас 30 секунд и не
растягивает время жизни кода до минут.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP_SECONDS = 30
# Число соседних шагов, которые тоже принимаются. Закрывает расхождение часов.
WINDOW_STEPS = 1
SECRET_BYTES = 20  # 160 бит, как советует RFC 4226 для HMAC-SHA1.


def generate_secret() -> str:
    """Новый секрет в base32 без заполняющих знаков: их не принимают телефоны."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode(secret: str) -> bytes:
    padded = secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded, casefold=True)


def code_at(secret: str, moment: float | None = None) -> str:
    """Код для указанного момента. Без аргумента — код на сейчас."""
    counter = int((time.time() if moment is None else moment) // STEP_SECONDS)
    return _code_for_counter(_decode(secret), counter)


def _code_for_counter(key: bytes, counter: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # Динамическое усечение RFC 4226: младшие четыре бита последнего байта
    # указывают, откуда взять четыре байта результата.
    offset = digest[-1] & 0x0F
    (number,) = struct.unpack(">I", digest[offset : offset + 4])
    return str((number & 0x7FFF_FFFF) % (10**DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, moment: float | None = None) -> bool:
    """Сверяет код с секретом. Битый секрет или не цифры дают отказ.

    Сравнение постоянное по времени: код живёт 30 секунд, и утечка его начала
    по времени ответа сокращала бы перебор.
    """
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return False
    try:
        key = _decode(secret)
    except (ValueError, TypeError):
        return False
    now = int((time.time() if moment is None else moment) // STEP_SECONDS)
    return any(
        hmac.compare_digest(_code_for_counter(key, now + shift), cleaned)
        for shift in range(-WINDOW_STEPS, WINDOW_STEPS + 1)
    )


def otpauth_url(secret: str, username: str, issuer: str = "АРМ ОДС") -> str:
    """Ссылка для аутентификатора. Её же кодирует QR-код.

    Телефон читает ссылку и заводит запись сам. Руками секрет вводить тоже
    можно: он приходит рядом той же строкой base32.
    """
    label = quote(f"{issuer}:{username}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
