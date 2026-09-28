-- Точность и полнота наивного правила рядом с моделью.
--
-- Дашборд сравнивает модель не с умолчаниями ТЗ 0,7 и 0,5, а с правилом
-- «событие было вчера» на той же выборке. Правило считается тем же замером,
-- что и модель, и лежит в `metrics.json` рядом с ней.

ALTER TABLE model_metric ADD COLUMN baseline_precision double precision;
ALTER TABLE model_metric ADD COLUMN baseline_recall double precision;
