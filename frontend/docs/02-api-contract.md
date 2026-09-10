# Контракт REST API

База: `VITE_API_BASE_URL`, по умолчанию `/api/v1`. Все списки — конверт
`{ items, page, pageSize, total }`.

Источник истины в коде: `src/shared/api/endpoints.ts` (пути) и
`src/shared/api/schemas.ts` (формы ответов). Этот документ — их человекочитаемая
проекция; при расхождении прав код, но расхождение — баг, который надо чинить.

## Железное правило

**Любой новый сетевой вызов — это ровно три правки в одном коммите:**

1. путь в `src/shared/api/endpoints.ts`;
2. zod-схема ответа в `src/shared/api/schemas.ts` (обязательно «мягкая», см. ниже);
3. MSW-хендлер в `src/mocks/handlers.ts` + данные в `src/mocks/db/`.

Пропустил третий пункт — приложение перестало работать на моках, и демо на защите
превращается в отладку бэкенда при жюри.

## Мягкие схемы

Все DTO объявляются через `z.looseObject({...})` (в zod 3 это называлось
`.passthrough()`). Неизвестное поле в ответе — не ошибка, оно сохраняется в объекте.

Разбор идёт через `parseTolerant()` в `src/shared/api/client.ts`:

```ts
const r = schema.safeParse(raw)
if (r.success) return r.data
console.warn('[api] ответ не прошёл разбор', { url, issues: r.error.issues })
return raw as T           // отдаём как есть, а не бросаем
```

То есть даже переименованное обязательное поле не роняет экран: компонент получит
`undefined` и покажет прочерк. Это осознанный размен строгости на живучесть на время
хакатона. См. [adr/0006-tolerant-parsing.md](adr/0006-tolerant-parsing.md).

## Ручки

### Метаданные

```
GET /meta → AppMeta
```

Грузится один раз при старте, `staleTime: Infinity`. Описывает всю предметную
область: направления, уровни риска, состав колонок журнала, состав виджетов
дашборда, справочники причин.

```ts
interface AppMeta {
  directions: DirectionMeta[]        // { code, label, shortLabel, accent, minHorizonHours }
  riskLevels: RiskLevelMeta[]        // { code, label, colorVar, order }
  statuses: StatusMeta[]             // { code, label, scope: 'prediction' | 'order' }
  districts: { code, label }[]       // округа для фильтра
  journalColumns: string[]           // ключи колонок журнала в нужном порядке
  orderColumns: string[]             // ключи колонок заявок в нужном порядке
  dashboardWidgets: string[]         // коды виджетов в нужном порядке
  reasons: Record<string, { code: string; label: string }[]>
}
```

`reasons` — справочники для полей формы типа `select`: ключ словаря совпадает с
`optionsRef` в `FieldDef`.

`statuses` различает сущности полем `scope`: один и тот же код может встретиться
дважды. Сейчас так ведёт себя только `REJECTED`. Терминальные статусы разные:
у заявки `DONE`, у прогноза `CLOSED`.

### Прогнозы

```
GET  /predictions?direction&level&status&district&from&to&sort&page&pageSize
     → { items: Prediction[], page, pageSize, total }
GET  /predictions/{id}                → PredictionDetail
GET  /predictions/{id}/timeseries     → { series: [{ name, unit?, points: [{t, v}] }],
                                          markerAt? }
POST /predictions/{id}/actions/{code} → PredictionDetail
```

Параметры `direction`, `level`, `status` — повторяемые (`?direction=A&direction=B`),
это мультивыбор. `sort` — `field:asc` / `field:desc`.

У таймсерий параметров нет: окно выбирает сервер, а карточка рисует то, что
пришло. `markerAt` — момент прогноза, вертикальная отметка на графике.

`PredictionDetail` = `Prediction` + `blocks: CardBlock[]` + `actions: ActionDef[]`.

### Единый эндпоинт действий

```
POST /predictions/{id}/actions/{code}
POST /orders/{id}/actions/{code}
тело = плоский объект значений полей формы
ответ = обновлённая сущность целиком
```

Не заводить ручку под кнопку. Бэкенд знает, что делать с кодом; фронт знает только
`ActionDef` из ответа. Ответ — сущность целиком, чтобы фронт не угадывал, что
изменилось: он просто кладёт её в кэш.
См. [adr/0004-single-action-endpoint.md](adr/0004-single-action-endpoint.md).

### Объекты и карта

```
GET /facilities?bbox&direction&level&district → GeoJSON FeatureCollection
GET /facilities/lines                         → GeoJSON FeatureCollection (LineString)
GET /facilities/{id}                          → FacilityRef
```

`properties` каждой точки содержит `facilityId`, `level`, `direction`,
`predictionId`, `probability`, `address`, `collector` — этого хватает и для
раскраски, и для клика, и для всплывающей подписи.

`bbox` — `minLon,minLat,maxLon,maxLat`. Карта сообщает границы после остановки
(`moveend`) и округляет их до 0.05°, иначе каждый пиксель панорамирования давал
бы новый ключ кэша и новый запрос.

Карта не фильтрует по `status` и по периоду: она показывает риск, а не стадию
работы с ним. Поэтому `facilityParams` эти поля не шлёт, а `FilterBar` на карте
получает `withStatus={false}` и `withPeriod={false}` — интерфейс не предлагает
фильтр, который не действует.

Трассы коллекторов лежат на отдельной ручке `/facilities/lines`, а не в ответе
`/facilities`. Геометрия сети не меняется, а точки опрашиваются раз в минуту:
возить одно вместе с другим значит гонять неизменную геометрию 60 раз в час.
Хук `useFacilityLines` берёт её один раз за сессию (`staleTime: Infinity`).

### Заявки

```
GET  /orders?status&dueBefore&sort&page&pageSize → { items: WorkOrder[], ... }
GET  /orders/{id}                                 → WorkOrder
POST /orders/{id}/actions/{code}                  → WorkOrder
```

Закрытие заявки (`code = 'close'`) обязано принести `outcome.predictionConfirmed`.
Именно эта отметка — источник честных Precision и Recall на реальных данных.

Заявка при этом переходит в `DONE`, связанный прогноз — в `CLOSED`. Терминальные
статусы у двух сущностей разные намеренно.

### Метрики и дашборд

```
GET /metrics/models      → ModelMetric[]   { direction, precision, recall,
                                             targetPrecision, targetRecall, evaluatedAt }
GET /metrics/pipeline    → { lastRunAt, lastRunMs, freshnessMinutes,
                             maxComputeMs, minHorizonHours,
                             targetComputeMs, targetHorizonHours }
GET /dashboard/summary   → { byLevel, byDirection, byStatus, byOrderStatus, total }
GET /dashboard/top-risks?limit=10 → Prediction[]
```

`byLevel`, `byDirection`, `byStatus`, `byOrderStatus` — это `Record<string, number>`,
а не массивы с фиксированными ключами. Появилось направление — появился ключ,
дашборд подхватил.

Целевые значения ТЗ (`targetPrecision`, `targetRecall`, `targetComputeMs`,
`targetHorizonHours`) приходят с сервера. Фронт их не хардкодит: поменялось ТЗ —
поменялся ответ, а не код.

## Как добавить ручку: пример целиком

Задача: показать в карточке историю ремонтов объекта.

```ts
// 1. src/shared/api/endpoints.ts
export const endpoints = {
  // ...
  facilityRepairs: (id: string) => `/facilities/${id}/repairs`,
}

// 2. src/shared/api/schemas.ts
export const RepairSchema = z.looseObject({
  id: z.string(),
  at: z.string(),
  kind: z.string(),
  comment: z.string().optional(),
})
export const RepairListSchema = z.array(RepairSchema)
export type Repair = z.infer<typeof RepairSchema>

// 3. src/shared/api/queries.ts
export function useFacilityRepairs(id: string) {
  return useQuery({
    queryKey: queryKeys.facilityRepairs(id),
    queryFn: () => apiGet(endpoints.facilityRepairs(id), RepairListSchema),
  })
}

// 4. src/mocks/handlers.ts
http.get(url('/facilities/:id/repairs'), async ({ params }) =>
  delayed(db().repairsByFacility[params.id as string] ?? [])),
```

Четвёртый шаг — обязательный. Без него `VITE_USE_MOCKS=true` перестаёт быть правдой.

## Переключение на реальный бэкенд

`.env`:

```
VITE_USE_MOCKS=false
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

Больше ничего менять не нужно: воркер MSW просто не поднимается.
