"""Хеш пароля. Стандартная библиотека, без внешних зависимостей.

Алгоритм scrypt лежит в `hashlib` и требует памяти, а не только времени. Это и
есть его смысл: перебор на видеокарте упирается в память и теряет
преимущество перед обычным процессором.

Внешней библиотеки здесь нет намеренно. Спецификация §2 считает зависимости
работающего процесса, и `passlib` с `bcrypt` добавили бы две ради тридцати
строк, которые уже есть в стандартной библиотеке.

Формат записи: `scrypt$n$r$p$соль$хеш`. Параметры лежат в самой строке, поэтому
их можно поднять позже, не ломая старые записи.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Стоимость. 128 × n × r = 16 МиБ памяти на одну проверку, около 60 мс на
# обычном процессоре. Вход бывает раз в смену, поэтому цена незаметна.
COST_N = 1 << 14
BLOCK_R = 8
PARALLEL_P = 1
KEY_LENGTH = 32
SALT_LENGTH = 16


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_LENGTH)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_LENGTH)
    digest = _derive(password, salt, COST_N, BLOCK_R, PARALLEL_P)
    return f"scrypt${COST_N}${BLOCK_R}${PARALLEL_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Сверяет пароль с записью.

    Битая запись даёт отказ, а не исключение: строку в базе мог испортить
    перенос данных, и падение процесса от этого хуже отказа во входе.

    Сравнение идёт через `compare_digest`: обычное сравнение строк выходит на
    первом несовпавшем байте и выдаёт длину общего начала по времени ответа.
    """
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        computed = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(computed, base64.b64decode(digest))
