"""Адрес тестовой базы. Читается раньше, чем приложение импортирует конфигурацию.

Этот файл лежит в корне проекта, а не в `tests/`. Pytest читает корневой
`conftest.py` до любого модуля тестов, поэтому переменная окружения успевает
встать на место. Объект `app.config.config` создаётся один раз при импорте
модуля: поставить переменную позже уже поздно.

Причина описана в ADR 0005. Тесты снимают базу целиком, а умолчание
`DATABASE_URL` указывало на рабочую базу `arm`.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

# Суффикс отличает тестовую базу от рабочей. Другого признака нет: пустая база
# выглядит так же, как база после неудачного прогона.
TEST_SUFFIX = "_test"

DEFAULT_DATABASE_URL = "postgresql+psycopg://arm:arm@127.0.0.1:5432/arm"


def resolve_test_database_url() -> str:
    """Отдаёт адрес тестовой базы.

    Правило одно. Переменная `TEST_DATABASE_URL` названа значит берётся она.
    Иначе к имени базы из `DATABASE_URL` добавляется суффикс `_test`. Имя уже
    кончается на суффикс значит адрес остаётся прежним.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    url = make_url(os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    name = url.database or ""
    if name.endswith(TEST_SUFFIX):
        return url.render_as_string(hide_password=False)
    return url.set(database=f"{name}{TEST_SUFFIX}").render_as_string(hide_password=False)


os.environ["DATABASE_URL"] = resolve_test_database_url()
