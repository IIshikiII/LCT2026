"""Таблицы схемы. Порядок и имена повторяют migrations/."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def _ts(name: str, **kwargs: object) -> Column[object]:
    """Метка времени со сдвигом. Все метки в схеме хранятся так."""
    return Column(name, DateTime(timezone=True), **kwargs)  # type: ignore[arg-type]


facility = Table(
    "facility",
    metadata,
    Column("id", Text, primary_key=True),
    Column("collector", Text, nullable=False),
    Column("section", Text),
    Column("chamber", Text),
    Column("device", Text),
    Column("district", Text, nullable=False),
    Column("address", Text, nullable=False),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("facility_type", Text, nullable=False),
    Column("commissioned_at", Date),
    Column("is_active", Boolean, nullable=False, default=True),
)

sensor = Table(
    "sensor",
    metadata,
    Column("id", Text, primary_key=True),
    Column("facility_id", Text, nullable=False),
    Column("sensor_type", Text, nullable=False),
    Column("installed_at", Date),
    Column("replaced_at", Date),
    Column("is_active", Boolean, nullable=False, default=True),
)

alarm_event = Table(
    "alarm_event",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("sensor_id", Text, nullable=False),
    Column("facility_id", Text, nullable=False),
    _ts("occurred_at", primary_key=True, nullable=False),
    Column("alarm_type", Text, nullable=False),
    Column("is_false", Boolean),
    Column("check_result", Text),
)

sensor_reading = Table(
    "sensor_reading",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("sensor_id", Text, nullable=False),
    Column("facility_id", Text, nullable=False),
    Column("metric", Text, nullable=False),
    _ts("observed_at", primary_key=True, nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", Text),
)

fault_log = Table(
    "fault_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("sensor_id", Text),
    Column("facility_id", Text, nullable=False),
    _ts("reported_at", nullable=False),
    Column("fault_type", Text, nullable=False),
    Column("note", Text),
    _ts("resolved_at"),
)

repair = Table(
    "repair",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("facility_id", Text, nullable=False),
    Column("sensor_id", Text),
    Column("work_type", Text, nullable=False),
    Column("planned_at", Date),
    Column("done_at", Date),
    Column("is_scheduled", Boolean, nullable=False, default=True),
    Column("note", Text),
)

permit = Table(
    "permit",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("facility_id", Text, nullable=False),
    Column("organisation", Text, nullable=False),
    Column("work_type", Text, nullable=False),
    _ts("starts_at", nullable=False),
    _ts("ends_at", nullable=False),
    Column("is_hot_work", Boolean, nullable=False, default=False),
)

inspection = Table(
    "inspection",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("facility_id", Text, nullable=False),
    Column("inspected_at", Date, nullable=False),
    Column("wear_score", Float),
    Column("defects", Text),
    Column("inspector", Text),
)

weather_hourly = Table(
    "weather_hourly",
    metadata,
    _ts("observed_at", primary_key=True, nullable=False),
    Column("district", Text, primary_key=True, nullable=False),
    Column("temperature_c", Float),
    Column("humidity", Float),
    Column("precip_mm", Float),
)

# direction, level и status — свободные строки. Ни Enum, ни CHECK со списком
# значений: пятое направление обязано появиться без миграции.
prediction = Table(
    "prediction",
    metadata,
    Column("id", Text, primary_key=True),
    Column("direction", Text, nullable=False),
    Column("facility_id", Text, nullable=False),
    Column("probability", Float, nullable=False),
    Column("level", Text, nullable=False),
    Column("horizon_hours", Integer, nullable=False),
    _ts("computed_at", nullable=False),
    _ts("computed_at_bucket", nullable=False),
    Column("compute_ms", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("summary", Text, nullable=False, default=""),
    Column("blocks", JSONB, nullable=False, default=list),
    Column("model_version", Text),
    Column("run_id", BigInteger),
)

# prediction_id обязателен: форма закрытия заявки берёт список причин по
# направлению прогноза. Миграция 002 сняла с колонки признак nullable.
work_order = Table(
    "work_order",
    metadata,
    Column("id", Text, primary_key=True),
    Column("number", Text, nullable=False, unique=True),
    Column("prediction_id", Text, nullable=False),
    Column("facility_id", Text, nullable=False),
    Column("work_type", Text, nullable=False),
    _ts("due_at", nullable=False),
    Column("status", Text, nullable=False),
    _ts("created_at", nullable=False),
    Column("outcome", JSONB),
)

action_log = Table(
    "action_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("action_code", Text, nullable=False),
    Column("actor", Text, nullable=False, default="dispatcher"),
    Column("payload", JSONB, nullable=False, default=dict),
    _ts("created_at", nullable=False),
)

pipeline_run = Table(
    "pipeline_run",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _ts("started_at", nullable=False),
    _ts("finished_at"),
    Column("duration_ms", Integer),
    Column("prediction_count", Integer, nullable=False, default=0),
    Column("model_versions", JSONB, nullable=False, default=dict),
    Column("status", Text, nullable=False, default="RUNNING"),
    Column("error", Text),
)

model_metric = Table(
    "model_metric",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("direction", Text, nullable=False),
    Column("precision_value", Float, nullable=False),
    Column("recall_value", Float, nullable=False),
    Column("target_precision", Float, nullable=False, default=0.7),
    Column("target_recall", Float, nullable=False, default=0.5),
    _ts("evaluated_at", nullable=False),
    Column("method", Text, nullable=False, default="offline_holdout"),
    Column("model_version", Text),
)

schema_migration = Table(
    "schema_migration",
    metadata,
    Column("version", Text, primary_key=True),
    Column("filename", Text, nullable=False),
    _ts("applied_at", nullable=False),
)
