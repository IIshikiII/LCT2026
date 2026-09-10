-- Начальная схема. Спецификация §6.
-- Направление, уровень и статус — свободные строки. Ни ENUM, ни CHECK со списком
-- значений: пятое направление обязано появиться без миграции.

CREATE TABLE facility (
    id              text PRIMARY KEY,
    collector       text NOT NULL,
    section         text,
    chamber         text,
    device          text,
    district        text NOT NULL,
    address         text NOT NULL,
    lat             double precision NOT NULL,
    lon             double precision NOT NULL,
    facility_type   text NOT NULL,
    commissioned_at date,
    is_active       boolean NOT NULL DEFAULT true
);

CREATE INDEX facility_lon_lat_idx ON facility (lon, lat);
CREATE INDEX facility_district_idx ON facility (district);

CREATE TABLE sensor (
    id            text PRIMARY KEY,
    facility_id   text NOT NULL REFERENCES facility (id),
    sensor_type   text NOT NULL,
    installed_at  date,
    replaced_at   date,
    is_active     boolean NOT NULL DEFAULT true
);

CREATE INDEX sensor_facility_idx ON sensor (facility_id);

-- Основная таблица. Миллионы строк. Партиционирование по месяцу.
CREATE TABLE alarm_event (
    id            bigserial,
    sensor_id     text NOT NULL,
    facility_id   text NOT NULL,
    occurred_at   timestamptz NOT NULL,
    alarm_type    text NOT NULL,
    is_false      boolean,
    check_result  text,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Запасная партиция. Помесячные партиции создаёт app/pipeline при загрузке данных.
CREATE TABLE alarm_event_default PARTITION OF alarm_event DEFAULT;

CREATE INDEX alarm_event_sensor_time_idx ON alarm_event (sensor_id, occurred_at);
CREATE INDEX alarm_event_facility_time_idx ON alarm_event (facility_id, occurred_at);
CREATE INDEX alarm_event_time_brin_idx ON alarm_event USING brin (occurred_at);

CREATE TABLE fault_log (
    id           bigserial PRIMARY KEY,
    sensor_id    text REFERENCES sensor (id),
    facility_id  text NOT NULL REFERENCES facility (id),
    reported_at  timestamptz NOT NULL,
    fault_type   text NOT NULL,
    note         text,
    resolved_at  timestamptz
);

CREATE INDEX fault_log_facility_time_idx ON fault_log (facility_id, reported_at);

CREATE TABLE repair (
    id           bigserial PRIMARY KEY,
    facility_id  text NOT NULL REFERENCES facility (id),
    sensor_id    text REFERENCES sensor (id),
    work_type    text NOT NULL,
    planned_at   date,
    done_at      date,
    is_scheduled boolean NOT NULL DEFAULT true,
    note         text
);

CREATE INDEX repair_facility_idx ON repair (facility_id, done_at);

CREATE TABLE permit (
    id            bigserial PRIMARY KEY,
    facility_id   text NOT NULL REFERENCES facility (id),
    organisation  text NOT NULL,
    work_type     text NOT NULL,
    starts_at     timestamptz NOT NULL,
    ends_at       timestamptz NOT NULL,
    is_hot_work   boolean NOT NULL DEFAULT false
);

CREATE INDEX permit_facility_window_idx ON permit (facility_id, starts_at, ends_at);

CREATE TABLE inspection (
    id            bigserial PRIMARY KEY,
    facility_id   text NOT NULL REFERENCES facility (id),
    inspected_at  date NOT NULL,
    wear_score    double precision,
    defects       text,
    inspector     text
);

CREATE INDEX inspection_facility_idx ON inspection (facility_id, inspected_at);

CREATE TABLE weather_hourly (
    observed_at   timestamptz NOT NULL,
    district      text NOT NULL,
    temperature_c double precision,
    humidity      double precision,
    precip_mm     double precision,
    PRIMARY KEY (observed_at, district)
);

CREATE TABLE prediction (
    id                 text PRIMARY KEY,
    direction          text NOT NULL,
    facility_id        text NOT NULL REFERENCES facility (id),
    probability        double precision NOT NULL,
    level              text NOT NULL,
    horizon_hours      integer NOT NULL,
    computed_at        timestamptz NOT NULL,
    computed_at_bucket timestamptz NOT NULL,
    compute_ms         integer NOT NULL,
    status             text NOT NULL,
    summary            text NOT NULL DEFAULT '',
    blocks             jsonb NOT NULL DEFAULT '[]'::jsonb,
    model_version      text,
    run_id             bigint
);

-- Идемпотентность прогона. Спецификация §7.
CREATE UNIQUE INDEX prediction_unique_idx
    ON prediction (facility_id, direction, computed_at_bucket);
CREATE INDEX prediction_direction_level_idx ON prediction (direction, level);
CREATE INDEX prediction_computed_at_idx ON prediction (computed_at DESC);
CREATE INDEX prediction_status_idx ON prediction (status);

CREATE TABLE work_order (
    id             text PRIMARY KEY,
    number         text NOT NULL UNIQUE,
    prediction_id  text REFERENCES prediction (id),
    facility_id    text NOT NULL REFERENCES facility (id),
    work_type      text NOT NULL,
    due_at         timestamptz NOT NULL,
    status         text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    outcome        jsonb
);

CREATE INDEX work_order_status_due_idx ON work_order (status, due_at);
CREATE INDEX work_order_prediction_idx ON work_order (prediction_id);

CREATE TABLE action_log (
    id           bigserial PRIMARY KEY,
    entity_type  text NOT NULL,
    entity_id    text NOT NULL,
    action_code  text NOT NULL,
    actor        text NOT NULL DEFAULT 'dispatcher',
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX action_log_entity_idx ON action_log (entity_type, entity_id, created_at DESC);

CREATE TABLE pipeline_run (
    id                bigserial PRIMARY KEY,
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz,
    duration_ms       integer,
    prediction_count  integer NOT NULL DEFAULT 0,
    model_versions    jsonb NOT NULL DEFAULT '{}'::jsonb,
    status            text NOT NULL DEFAULT 'RUNNING',
    error             text
);

CREATE INDEX pipeline_run_started_idx ON pipeline_run (started_at DESC);

CREATE TABLE model_metric (
    id               bigserial PRIMARY KEY,
    direction        text NOT NULL,
    precision_value  double precision NOT NULL,
    recall_value     double precision NOT NULL,
    target_precision double precision NOT NULL DEFAULT 0.7,
    target_recall    double precision NOT NULL DEFAULT 0.5,
    evaluated_at     timestamptz NOT NULL,
    method           text NOT NULL DEFAULT 'offline_holdout',
    model_version    text
);

CREATE INDEX model_metric_direction_time_idx ON model_metric (direction, evaluated_at DESC);
