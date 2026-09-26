-- Загрузчик выгрузки и поток СМВУ. ADR 0017.
--
-- 1. Единица прогноза. Выгрузка знает объект, галерею и пикет. Строка
--    `facility` теперь держит их полями. Пожар и подтопление считают пикет,
--    доступ считает участок вокруг узла входа (ADR 0001). Вид строки лежит в
--    `kind`. Поля `collector` (объект) и `district` (комплекс) остаются: их
--    читают признаки и API. Текстовое `section` остаётся подписью для
--    карточки, номер участка доступа лежит в `section_no`.
-- 2. Канал. Датчик несёт номер канала выгрузки и его тип по справочнику.
--    По ним приём потока находит датчик и переводит значение в код события.
-- 3. Поток. Событие потока помнит номер события источника и момент приёма.
--    Задержку потока меряет конвейер: от метки времени события до конца
--    прогона, который его учёл.
-- 4. Показания. Температура и метан хранятся часом: наибольшее значение
--    канала за час. Уникальный ключ часа держит повторный приём.

ALTER TABLE facility
    ADD COLUMN kind       text,
    ADD COLUMN object_id  integer,
    ADD COLUMN gallery    integer,
    ADD COLUMN picket     double precision,
    ADD COLUMN section_no integer,
    ADD COLUMN chainage_m double precision;

CREATE INDEX facility_collector_idx ON facility (collector);
CREATE INDEX facility_kind_idx ON facility (kind);

ALTER TABLE sensor
    ADD COLUMN channel_id   bigint,
    ADD COLUMN channel_type text,
    ADD COLUMN channel_name text;

CREATE UNIQUE INDEX sensor_channel_idx ON sensor (channel_id, facility_id);

ALTER TABLE alarm_event
    ADD COLUMN source_event_id bigint,
    ADD COLUMN received_at     timestamptz;

CREATE INDEX alarm_event_received_idx ON alarm_event (received_at)
    WHERE received_at IS NOT NULL;

CREATE UNIQUE INDEX sensor_reading_hour_idx
    ON sensor_reading (sensor_id, metric, observed_at);

-- Состояние загрузки. Одна строка на ключ.
CREATE TABLE ingest_state (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pipeline_run
    ADD COLUMN stream_events  integer,
    ADD COLUMN stream_lag_ms  integer;
