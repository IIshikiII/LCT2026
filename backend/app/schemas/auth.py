"""DTO входа в систему. Зеркало `frontend/src/shared/api/schemas.ts`."""

from __future__ import annotations

from app.schemas.base import Dto


class LoginRequest(Dto):
    username: str
    password: str


class LoginResponse(Dto):
    """Итог первого шага.

    Поле `status` принимает `MFA_REQUIRED` или `ENROLL_REQUIRED`. Секрет и
    ссылка приходят только во втором случае и только один раз.
    """

    status: str
    mfa_token: str
    secret: str | None = None
    otpauth_url: str | None = None


class MfaRequest(Dto):
    mfa_token: str
    code: str


class CurrentUser(Dto):
    """Кто вошёл. Фронт берёт отсюда подпись в шапке и границы видимости."""

    username: str
    full_name: str
    role: str
    role_label: str
    scope_kind: str
    scope_value: str | None = None
    # Коды действий, доступных роли. Кнопки фронт всё равно рисует из `actions`
    # карточки, а этот список нужен подсказке «почему действий нет».
    permissions: list[str]


class SessionResponse(Dto):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: CurrentUser
