-- Происхождение заявки полем, а не статусом. ADR 0006, поправка.
--
-- Статус `MANUAL_CREATED` описывал не состояние, а происхождение. После того
-- как решение диспетчера стало создавать заявку сразу подтверждённой, состояние
-- у обеих заявок одно, и различает их только то, кто их породил.
--
-- Та же причина, по которой решение диспетчера ушло из названия статуса в поля:
-- статус отвечает за место в работе, а не за историю появления.

ALTER TABLE work_order ADD COLUMN created_by text NOT NULL DEFAULT 'PIPELINE';

UPDATE work_order SET created_by = 'DISPATCHER' WHERE status = 'MANUAL_CREATED';
UPDATE work_order SET status = 'CONFIRMED' WHERE status = 'MANUAL_CREATED';
