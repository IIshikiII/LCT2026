-- Одна открытая заявка на объект и направление. ADR 0022.
--
-- Правило держали код конвейера и код решения диспетчера. Индекс держит его
-- для любого пути записи, в том числе будущего. Направление заявки раньше
-- выводилось через прогноз, а индекс строится по колонкам одной таблицы,
-- поэтому оно ложится в заявку.

ALTER TABLE work_order ADD COLUMN direction text;

UPDATE work_order w
SET direction = p.direction
FROM prediction p
WHERE p.id = w.prediction_id AND w.direction IS NULL;

CREATE UNIQUE INDEX work_order_one_open_idx
    ON work_order (facility_id, direction)
    WHERE status NOT IN ('CLOSED_CONFIRMED', 'CLOSED_NOT_CONFIRMED', 'REJECTED')
      AND direction IS NOT NULL;
