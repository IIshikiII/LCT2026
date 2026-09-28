-- Экспертные правила пожарного риска. ADR 0016.
--
-- 1. Пусконаладка объекта. Новая система пожарной сигнализации шумит месяцами
--    (объект 5962 «Гамма ПС», ответ заказчика). Эксплуатация знает такой
--    объект заранее и ставит дату конца пусконаладки. До этой даты правила
--    понижают уровень сигналов объекта на ступень. CRITICAL не понижается.
-- 2. Окна плановых работ. График ППР даёт даты демонтажа и поверки датчиков
--    метана, график ТО даёт месяцы. Газ внутри окна правила не читают как
--    загазованность: это проверка газом из баллона.

ALTER TABLE collector ADD COLUMN commissioning_until date;

CREATE TABLE maintenance_window (
    id          bigserial PRIMARY KEY,
    -- Объект, код из `collector.code`.
    collector   text NOT NULL,
    -- PPR_METHANE: демонтаж и поверка датчиков метана. TO: техническое
    -- обслуживание по графику ТО.
    kind        text NOT NULL,
    starts_on   date NOT NULL,
    ends_on     date NOT NULL,
    -- Откуда окно: имя файла графика или «журнал» для восстановленных обходов.
    source      text NOT NULL,
    CHECK (ends_on >= starts_on)
);

CREATE INDEX maintenance_window_collector ON maintenance_window (collector, starts_on);
