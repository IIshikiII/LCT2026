-- Учётные записи, роли и второй фактор. ADR 0007.
--
-- ТЗ §11 держит ролевую модель, федерацию с LDAP/AD и журналирование действий
-- в обязательных требованиях. Настоящий каталог Active Directory заказчик
-- отдать не может, поэтому таблица играет его роль: та же пара «логин и роль»,
-- то же разграничение по области видимости. Поле `directory` называет, откуда
-- пришла запись, и оставляет место для записей из каталога.
--
-- Пароль здесь только у записей `LOCAL`. Запись из каталога проверяет пароль
-- на стороне каталога, и колонка у неё остаётся пустой.

CREATE TABLE app_user (
    username      text PRIMARY KEY,
    full_name     text NOT NULL,
    role          text NOT NULL,
    -- Область видимости. `ALL` — всё предприятие, `DISTRICT` — район,
    -- `COMPLEX` — один комплекс. Значение лежит в `scope_value`.
    scope_kind    text NOT NULL DEFAULT 'ALL',
    scope_value   text,
    password_hash text,
    -- Секрет второго фактора в base32. Ставится при регистрации ключа.
    totp_secret   text,
    mfa_enrolled  boolean NOT NULL DEFAULT false,
    is_active     boolean NOT NULL DEFAULT true,
    directory     text NOT NULL DEFAULT 'LOCAL',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

-- Список диспетчеров района нужен фильтру журнала и разбору прав.
CREATE INDEX app_user_role_idx ON app_user (role);

-- Журнал действий пишет имя из токена, а не константу. Старые строки помнят
-- заглушку, и оставлять её значит утверждать, что действие выполнил человек с
-- таким именем. Имя заменяется на признак того, что автора не записывали.
UPDATE action_log SET actor = 'UNKNOWN' WHERE actor = 'dispatcher';

-- Аудит ТЗ §11 обязан искаться по человеку и по времени, а не только по
-- сущности. Вход в систему лежит в той же таблице с `entity_type = 'auth'`.
CREATE INDEX action_log_actor_idx ON action_log (actor, created_at DESC);
