"""Признаки направлений. Реестр связывает имя признака с построителем.

Регистрация идёт здесь, один раз на процесс. Модуль направления только
объявляет словарь построителей и ничего не импортирует из конвейера.
"""

from app.features import registry


def _register_access() -> None:
    from app.features.access_daily import BUILDERS, DIRECTION

    registry.register(DIRECTION, BUILDERS)


if not registry.known("UNAUTHORIZED_ACCESS"):
    _register_access()
