-- Показания датчиков — источник для карточки прогноза и для признаков.
-- Спецификация §6.

CREATE TABLE sensor_reading (
    id           bigserial,
    sensor_id    text NOT NULL,
    facility_id  text NOT NULL,
    metric       text NOT NULL,
    observed_at  timestamptz NOT NULL,
    value        double precision NOT NULL,
    unit         text,
    PRIMARY KEY (id, observed_at)
) PARTITION BY RANGE (observed_at);

-- Запасная партиция. Помесячные создаёт загрузка данных.
CREATE TABLE sensor_reading_default PARTITION OF sensor_reading DEFAULT;

CREATE INDEX sensor_reading_facility_metric_time_idx
    ON sensor_reading (facility_id, metric, observed_at);
CREATE INDEX sensor_reading_sensor_time_idx
    ON sensor_reading (sensor_id, observed_at);
CREATE INDEX sensor_reading_time_brin_idx
    ON sensor_reading USING brin (observed_at);

-- Заявка всегда рождается из прогноза. Форма закрытия берёт список причин по
-- направлению прогноза, поэтому связь обязательная.
ALTER TABLE work_order ALTER COLUMN prediction_id SET NOT NULL;
