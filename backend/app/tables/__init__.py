"""Определения таблиц. SQLAlchemy Core, без ORM.

Зеркало файлов в `migrations/`. Схему создаёт SQL, а эти объекты нужны только
для построения запросов с параметризацией. Меняете миграцию — меняйте и файл
здесь, в том же коммите.
"""

from __future__ import annotations

from app.tables.core import (
    action_log,
    alarm_event,
    app_user,
    collector,
    facility,
    fault_log,
    inspection,
    metadata,
    model_metric,
    permit,
    pipeline_run,
    prediction,
    repair,
    schema_migration,
    sensor,
    sensor_reading,
    weather_hourly,
    work_order,
)

__all__ = [
    "action_log",
    "alarm_event",
    "app_user",
    "collector",
    "facility",
    "fault_log",
    "inspection",
    "metadata",
    "model_metric",
    "permit",
    "pipeline_run",
    "prediction",
    "repair",
    "schema_migration",
    "sensor",
    "sensor_reading",
    "weather_hourly",
    "work_order",
]
