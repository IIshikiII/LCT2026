---
name: arm-api-contract
description: Контракт REST API и правила его расширения во frontend/ (эндпоинты, zod-схемы, DTO). Применять при добавлении или изменении любого сетевого вызова, схемы ответа или мок-хендлера.
---

# Контракт API АРМ диспетчера ОДС

Полный источник — `ARM-ODS-frontend-spec.md` §6–7. База `/api/v1`.
Списки возвращают `{ items, page, pageSize, total }`.

## Железное правило

Любой новый вызов добавляется **тремя изменениями в одном коммите**:

1. Функция в `frontend/src/shared/api/endpoints.ts` (через общий `http` из
   `http.ts`).
2. `zod`-схема в `frontend/src/shared/api/schemas.ts` — **обязательно**
   `z.object({...}).passthrough()` и разбор через `safeParse`. Если разбор не
   прошёл — `console.warn` и вернуть данные как есть, **не бросать исключение**.
3. MSW-хендлер в `frontend/src/mocks/handlers.ts` + данные в `seed.ts`.

Рассинхрон «фронт добавил вызов, мок не добавил» — запрещён. Приложение обязано
работать на моках целиком (`VITE_USE_MOCKS=true`).

## Эндпоинты

```
GET  /meta                              → AppMeta (один раз при старте, хук useMeta)
GET  /predictions?direction&level&status&district&from&to&sort&page&pageSize
GET  /predictions/{id}                   → PredictionDetail
POST /predictions/{id}/actions/{code}    тело = значения полей формы действия
GET  /predictions/{id}/timeseries?from&to
GET  /facilities?bbox&direction&level    → GeoJSON FeatureCollection
GET  /facilities/{id}
GET  /orders?status&dueBefore&page&pageSize
GET  /orders/{id}
POST /orders/{id}/actions/{code}
GET  /metrics/models                     → ModelMetric[]
GET  /metrics/pipeline                   → { lastRunAt, lastRunMs, freshnessMinutes }
GET  /dashboard/summary
GET  /dashboard/top-risks?limit=10       → Prediction[]
```

## Единый эндпоинт действий

`POST /predictions/{id}/actions/{code}` и `POST /orders/{id}/actions/{code}` —
форма уходит как есть, сервер знает что делать с `code`. Ответ — обновлённый
`PredictionDetail` / `WorkOrder`, чтобы фронт не гадал, что изменилось.
**Не заводить отдельные ручки под кнопки.**

## Ключевые типы (полностью в spec §6)

`Direction`, `level`, `status` — **`string`, не union**. `CardBlock.data` —
`unknown`. `AppMeta` несёт `directions`, `riskLevels`, `journalColumns`,
`dashboardWidgets`, `reasons`. Запасные значения `AppMeta` — в
`frontend/src/shared/config/fallbacks.ts` на случай недоступного `/meta`.
