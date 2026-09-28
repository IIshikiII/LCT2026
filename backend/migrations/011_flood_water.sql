-- Модель подтопления ADR 0012 в бэкенде. ADR 0013.
--
-- 1. Погода. Модель читает дождь, снегопад и высоту снежного покрова, а не
--    только сумму осадков. Колонки пустые у старых строк: признак без данных
--    считается нулём или пропуском, как в обучении.
-- 2. Разметка суток. Сигнал «Затоплен» или «Не замкнут» оставляет и вода, и
--    плановая проверка. Классификатор ADR 0012 размечает каждые сутки пикета
--    с сигналом один раз, и признаки читают готовую разметку. Сутки берутся
--    по Москве, как в журнале СМВУ.

ALTER TABLE weather_hourly
    ADD COLUMN rain_mm      double precision,
    ADD COLUMN snowfall_cm  double precision,
    ADD COLUMN snow_depth_m double precision;

CREATE TABLE flood_water_day (
    facility_id text NOT NULL,
    day         date NOT NULL,
    -- Итог разметки: сутки с водой. Ложь значит плановая проверка.
    is_water    boolean NOT NULL,
    -- Сигнал ночью (22:00–6:00) или в нерабочий день. Такие сутки водой
    -- считаются без классификатора.
    is_labelled boolean NOT NULL,
    -- Вероятность воды после поправки Elkan и Noto. Пусто, когда
    -- классификатора не было и сработало только правило времени.
    p_water     double precision,
    labelled_at timestamptz NOT NULL,
    PRIMARY KEY (facility_id, day)
);

CREATE INDEX flood_water_day_day_idx ON flood_water_day (day);
