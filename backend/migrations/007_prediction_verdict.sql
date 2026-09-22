-- Решение диспетчера отдельными полями. ADR 0006.
--
-- Раньше решение жило в названии статуса, а исправленный уровень не хранился
-- вовсе: из отказа выводился только интервал «не ниже HIGH». Модель на
-- интервале не учится, на паре «предсказано MEDIUM, верно HIGH» учится.
--
-- Поле `assignee` отвечает ответу заказчика 3.6: «кто первый отработал, тот и
-- молодец, имя исполнителя в журнале обязательно».

ALTER TABLE prediction ADD COLUMN assignee text;
ALTER TABLE prediction ADD COLUMN verdict text;
ALTER TABLE prediction ADD COLUMN dispatcher_level text;
ALTER TABLE prediction ADD COLUMN decided_at timestamptz;

-- Выборка «все прогнозы, где диспетчер поднял уровень» нужна и журналу, и
-- дообучению, поэтому она обязана идти по индексу.
CREATE INDEX prediction_verdict_idx ON prediction (direction, verdict, dispatcher_level);
CREATE INDEX prediction_assignee_idx ON prediction (assignee) WHERE assignee IS NOT NULL;

-- Перевод прежних статусов. Соответствие описано в ADR 0006, раздел
-- «Последствия».
UPDATE prediction SET status = 'ORDER_OPEN' WHERE status = 'ORDER_CONFIRMED';
UPDATE prediction SET status = 'CLOSED_CONFIRMED' WHERE status = 'CLOSED';
UPDATE prediction
   SET status = 'DECIDED', verdict = 'CORRECTED'
 WHERE status = 'REJECTED';

-- Прежний терминальный статус заявки назывался DONE и не различал исход.
-- Исход лежал в `outcome->>'predictionConfirmed'`, поэтому перевод читает его.
UPDATE work_order
   SET status = CASE
       WHEN outcome->>'predictionConfirmed' = 'false' THEN 'CLOSED_NOT_CONFIRMED'
       ELSE 'CLOSED_CONFIRMED'
   END
 WHERE status = 'DONE';
