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

collector = Table(
    "collector",
    metadata,
    Column("code", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("district", Text),
    # Ломаная как массив пар [lon, lat]. Порядок тот же, что в GeoJSON.
    Column("line", JSONB, nullable=False),
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
    # Витрина для диспетчера. Ей разрешено быть неполной и битой.
    Column("blocks", JSONB, nullable=False, default=list),
    # Вход модели: имя признака -> число. Наружу не отдаётся.
    Column("features", JSONB, nullable=False, default=dict),
    # Момент снятия мьюта. Ставит диспетчер при отклонении.
    _ts("suppress_until"),
    # Решение диспетчера. Статус говорит, где прогноз в работе, а эти четыре
    # поля — что о нём решил человек. Разделение обосновано в ADR 0006.
    #
    # `verdict` принимает AGREED или CORRECTED, `dispatcher_level` держит
    # уровень, который диспетчер считает верным. Пара «уровень модели и уровень
    # диспетчера» и есть ярлык для дообучения, и он ставится на каждом прогнозе,
    # а не только там, где выехала бригада.
    Column("assignee", Text),
    Column("verdict", Text),
    Column("dispatcher_level", Text),
    _ts("decided_at"),
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
    # Кто породил заявку: `PIPELINE` по порогу уровня или `DISPATCHER` решением
    # по прогнозу. Это происхождение, а не состояние, поэтому поле, а не статус.
    Column("created_by", Text, nullable=False, default="PIPELINE"),
    _ts("created_at", nullable=False),
    Column("outcome", JSONB),
)

# Учётная запись. Замещает каталог Active Directory, которого нам не дадут:
# те же поля «логин, имя, роль, область видимости». ADR 0007.
app_user = Table(
    "app_user",
    metadata,
    Column("username", Text, primary_key=True),
    Column("full_name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("scope_kind", Text, nullable=False, default="ALL"),
    Column("scope_value", Text),
    Column("password_hash", Text),
    Column("totp_secret", Text),
    Column("mfa_enrolled", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("directory", Text, nullable=False, default="LOCAL"),
    # Номер набора тестового стенда. Пусто у настоящих записей. ADR 0007.
    Column("demo_set", Integer),
    _ts("created_at"),
    _ts("last_login_at"),
)

# `entity_type` принимает `prediction`, `order` и `auth`. Третье значение
# держит входы и выходы: ТЗ §11 требует журналировать все действия, а не
# только смену состояния.
action_log = Table(
    "action_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("action_code", Text, nullable=False),
    Column("actor", Text, nullable=False),
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
