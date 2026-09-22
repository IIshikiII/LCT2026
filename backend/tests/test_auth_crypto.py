"""Пароль, одноразовый код и токен. Проверки без базы и без HTTP.

Три механизма написаны на стандартной библиотеке, поэтому проверяются здесь по
отдельности: ошибка в любом из них молча открыла бы вход.
"""

from __future__ import annotations

import time

import pytest

from app.auth import passwords, totp
from app.auth.tokens import KIND_ACCESS, KIND_MFA, TokenError, sign, verify

SECRET = "ключ-подписи-для-теста"


# --- пароль ------------------------------------------------------------------


def test_the_same_password_gets_two_different_records() -> None:
    """Соль своя у каждой записи, иначе одинаковые пароли видны по хешу."""
    assert passwords.hash_password("одинаковый") != passwords.hash_password("одинаковый")


def test_a_correct_password_passes_and_a_wrong_one_does_not() -> None:
    stored = passwords.hash_password("Тверская-1")
    assert passwords.verify_password("Тверская-1", stored)
    assert not passwords.verify_password("Тверская-2", stored)


@pytest.mark.parametrize(
    "stored",
    ["", "мусор", "scrypt$мало$частей", "bcrypt$16384$8$1$c29sdA==$aGFzaA=="],
)
def test_a_broken_record_refuses_instead_of_raising(stored: str) -> None:
    """Испорченную запись мог оставить перенос данных. Падать на ней нельзя."""
    assert not passwords.verify_password("любой", stored)


# --- одноразовый код ---------------------------------------------------------


def test_the_code_is_six_digits() -> None:
    code = totp.code_at(totp.generate_secret())
    assert len(code) == totp.DIGITS and code.isdigit()


def test_the_code_of_this_moment_passes() -> None:
    secret = totp.generate_secret()
    now = time.time()
    assert totp.verify(secret, totp.code_at(secret, now), now)


def test_the_neighbouring_step_passes_and_the_distant_one_does_not() -> None:
    """Окно в один шаг закрывает расхождение часов и не растягивает код."""
    secret = totp.generate_secret()
    now = time.time()
    near = totp.code_at(secret, now - totp.STEP_SECONDS)
    far = totp.code_at(secret, now - totp.STEP_SECONDS * 5)

    assert totp.verify(secret, near, now)
    assert not totp.verify(secret, far, now)


@pytest.mark.parametrize("code", ["", "12345", "1234567", "абвгде", "12 34 56 78"])
def test_a_code_of_the_wrong_shape_refuses(code: str) -> None:
    assert not totp.verify(totp.generate_secret(), code, time.time())


def test_spaces_inside_a_code_are_forgiven() -> None:
    """Аутентификаторы показывают код парами цифр, и его копируют с пробелом."""
    secret = totp.generate_secret()
    now = time.time()
    code = totp.code_at(secret, now)
    assert totp.verify(secret, f"{code[:3]} {code[3:]}", now)


def test_the_rfc_test_vector_matches() -> None:
    """Вектор RFC 6238: секрет `12345678901234567890`, момент 59 секунд."""
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.code_at(secret, 59) == "287082"


def test_the_link_carries_the_secret_and_the_parameters() -> None:
    secret = totp.generate_secret()
    url = totp.otpauth_url(secret, "ods")

    assert url.startswith("otpauth://totp/")
    assert f"secret={secret}" in url
    assert "digits=6" in url and "period=30" in url


# --- токен -------------------------------------------------------------------


def test_a_signed_token_reads_back() -> None:
    token = sign({"sub": "ods", "kind": KIND_ACCESS}, SECRET, 60)
    assert verify(token, SECRET, KIND_ACCESS)["sub"] == "ods"


def test_another_key_does_not_open_the_token() -> None:
    token = sign({"sub": "ods", "kind": KIND_ACCESS}, SECRET, 60)
    with pytest.raises(TokenError):
        verify(token, "чужой ключ", KIND_ACCESS)


def test_a_changed_payload_breaks_the_signature() -> None:
    header, claims, signature = sign({"sub": "ods", "kind": KIND_ACCESS}, SECRET, 60).split(".")
    forged = f"{header}.{claims[:-2]}XX.{signature}"
    with pytest.raises(TokenError):
        verify(forged, SECRET, KIND_ACCESS)


def test_an_expired_token_does_not_pass() -> None:
    token = sign({"sub": "ods", "kind": KIND_ACCESS}, SECRET, -1)
    with pytest.raises(TokenError):
        verify(token, SECRET, KIND_ACCESS)


def test_the_intermediate_token_does_not_pass_as_a_session() -> None:
    """Главное свойство второго фактора: пароль один сессии не открывает."""
    token = sign({"sub": "ods", "kind": KIND_MFA}, SECRET, 60)
    with pytest.raises(TokenError):
        verify(token, SECRET, KIND_ACCESS)


@pytest.mark.parametrize("token", ["", "не.токен", "a.b.c.d", "a.b.c"])
def test_a_malformed_token_refuses(token: str) -> None:
    with pytest.raises(TokenError):
        verify(token, SECRET, KIND_ACCESS)
