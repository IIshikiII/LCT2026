---
name: arm-new-screen
description: Чеклист добавления экрана или крупного модуля во frontend/ АРМ диспетчера ОДС. Вызывать через /arm-new-screen <название>.
---

# Новый экран АРМ диспетчера ОДС

Периметр — 4 экрана: `/` (дашборд), `/map`, `/journal`, `/orders`. Новые экраны
не заводить, пока эти четыре не закончены (spec §13). Скилл — для сборки этих
четырёх и их частей.

## Структура (spec §5, плоская, без FSD)

```
frontend/src/
  app/          router.tsx, providers.tsx, AppShell.tsx
  screens/      dashboard/ map/ journal/ orders/
  card/         PredictionCard.tsx, blockRegistry.tsx, blocks/, actionRegistry.tsx, DynamicForm.tsx
  table/        DataTable.tsx, columnRegistry.tsx
  widgets/      widgetRegistry.tsx
  shared/       api/ ui/ lib/ config/
  mocks/        handlers.ts, seed.ts, browser.ts
```

## Чеклист экрана

1. Папка `frontend/src/screens/<name>/` с корневым компонентом.
2. Маршрут в `app/router.tsx`; пункт рельса в `AppShell`; флаг в
   `shared/config/features.ts`.
3. **Всё состояние UI — в параметрах URL** (`useSearchParams`), не в useState и
   не в глобальном сторе: фильтры, сортировка, `?prediction=<id>`. Ссылка на
   отфильтрованный экран с открытой карточкой обязана восстанавливать состояние.
4. Данные — через TanStack Query хук в `<name>/queries.ts`,
   `refetchInterval: 60_000`. Никакого WebSocket.
5. Новые вызовы API — по скиллу `arm-api-contract` (endpoint + zod + мок вместе).
6. Состояния: загрузка (скелет), ошибка (`ErrorState` — что случилось и как
   повторить), пусто (`EmptyState` — почему пусто).
7. Клавиатура: фокус виден, все действия доступны с клавиатуры, контраст ≥ AA.
8. Стиль — по скиллу `arm-design-tokens`.
9. Тест на ключевой сценарий экрана (Vitest + Testing Library).

## Порядок сборки проекта (spec §11)

каркас → `shared/api` + `useMeta` + fallbacks → моки → `shared/ui` →
`DataTable` + `columnRegistry` → журнал → карточка + `blockRegistry` →
`DynamicForm` + действия → карта → дашборд + `widgetRegistry` → заявки →
опрос, состояния ошибок/пустоты, доступность.
