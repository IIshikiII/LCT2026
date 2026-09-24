"""Признаки направлений. Реестр связывает имя признака с построителем.

Регистрация идёт здесь, один раз на процесс. Модуль направления только
объявляет словарь построителей и ничего не импортирует из конвейера.
"""

from app.features import registry


def _register_access() -> None:
    from app.features.access import HOURLY_BUILDERS, SHARED_BUILDERS
    from app.features.access_daily import BUILDERS as DAILY_BUILDERS
    from app.features.access_daily import DIRECTION

    # Слияние словарём, а не двумя вызовами: общий признак объявлен один раз,
    # и реестр не спорит сам с собой.
    registry.register(
        DIRECTION, {**SHARED_BUILDERS, **HOURLY_BUILDERS, **DAILY_BUILDERS}
    )


if not registry.known("UNAUTHORIZED_ACCESS"):
    _register_access()


def _register_flood() -> None:
    from app.features.flood import BUILDERS, DIRECTION

    registry.register(DIRECTION, BUILDERS)


if not registry.known("FLOOD_RISK"):
    _register_flood()
