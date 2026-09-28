# REST API

База адреса `/api/v1`. Описание OpenAPI отдаёт сам сервер: страница
`/api/v1/docs`, файл `/api/v1/openapi.json`. На локальной машине это
<http://localhost:8000/api/v1/docs>.

## Вход

1. Отправьте `POST /auth/login` с телом `{"username": "...", "password": "..."}`.
   Ответ несёт `mfaToken`.
2. Отправьте `POST /auth/mfa` с телом `{"mfaToken": "...", "code": "123456"}`.
   Ответ несёт `accessToken`.
3. Передавайте токен в заголовке `Authorization: Bearer <токен>`.

Без токена работают только `/healthz`, вход, страница OpenAPI и панель
тестового стенда. Ответ 401
значит, что токена нет или срок истёк. Ответ 403 значит, что роль не выполняет
это действие. Подробности лежат в [`backend/docs/09-auth.md`](../backend/docs/09-auth.md).

## Ручки

| Метод и путь | Что отдаёт |
|---|---|
| `GET /meta` | Описание предметной области: направления, уровни, статусы, колонки журнала, виджеты |
| `GET /dashboard/summary` | Счётчики по уровням и направлениям |
| `GET /dashboard/top-risks` | Объекты с наибольшим риском |
| `GET /predictions` | Журнал прогнозов с фильтрами и страницами |
| `GET /predictions/{id}` | Карточка прогноза: блоки объяснения и доступные действия |
| `GET /predictions/{id}/timeseries` | Ряды телеметрии для графика карточки |
| `POST /predictions/{id}/actions/{code}` | Действие диспетчера над прогнозом |
| `GET /orders` | Заявки на обслуживание |
| `GET /orders/{id}` | Одна заявка |
| `POST /orders/{id}/actions/{code}` | Действие над заявкой: назначить бригаду, закрыть |
| `GET /facilities` | Объекты для карты |
| `GET /facilities/lines` | Трассы коллекторов для карты |
| `GET /facilities/{id}` | Один объект |
| `GET /metrics/models` | Точность и полнота моделей рядом с наивным правилом |
| `GET /metrics/pipeline` | Время прогона, горизонт, задержка потока |
| `GET /alerts` | Критические прогнозы, которые никто не взял в работу |
| `GET /auth/me` | Текущий пользователь и его роль |
| `POST /auth/logout` | Выход |
| `POST /stream/events` | Приём потока СМВУ. Требует ключ `STREAM_TOKEN` |
| `GET`, `POST`, `DELETE /auth/test-accounts` | Наборы учётных записей тестового стенда. Работают при `TEST_STAND=true` |
| `GET /healthz` | Проверка живости, вне `/api/v1` |

## Полный контракт

| Документ | О чём |
|---|---|
| [`frontend/docs/02-api-contract.md`](../frontend/docs/02-api-contract.md) | Контракт со стороны интерфейса: схемы zod, правила разбора |
| [`backend/docs/04-api-required-by-frontend.md`](../backend/docs/04-api-required-by-frontend.md) | Каждая ручка поле в поле: параметры и формы ответов |
| [`backend/docs/04-api-layer.md`](../backend/docs/04-api-layer.md) | Роутеры, ошибки, таблица переходов статусов |
