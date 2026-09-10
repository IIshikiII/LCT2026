-- Момент снятия мьюта. Ставится при отклонении прогноза или его заявки.
-- Раньше срок давности отклонения считался от момента расчёта прогноза. Это
-- было приближение: диспетчер отклоняет прогноз позже, чем тот посчитан.
-- Теперь момент хранится явно и приходит от диспетчера.

ALTER TABLE prediction ADD COLUMN suppress_until timestamptz;

CREATE INDEX prediction_suppress_idx
    ON prediction (facility_id, direction, suppress_until);
