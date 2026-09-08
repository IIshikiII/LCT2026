# Точки расширения

Четыре реестра. Каждый — обычный объект `Record<string, Компонент>` с запасным
вариантом. Расширение = один файл плюс одна строка.

Общий принцип: **строка из API ищется в реестре; не нашлась — работает запасной
рендерер.** Никогда не `switch`, никогда не `if (direction === ...)`.

---

## 0. Направление прогнозирования

Самый частый вопрос: «как добавить пятое направление?»

**Ответ: правок в коде фронтенда не требуется вообще.**

Направление появляется в `GET /meta`:

```json
{ "code": "FLOOD_RISK", "label": "Риск подтопления",
  "shortLabel": "Подтоп", "accent": "#4A7FB5", "minHorizonHours": 24 }
```

и после этого само:

- добавляется в фильтр направлений журнала и карты (`FilterBar` перебирает
  `meta.directions`);
- появляется в виджете «разбивка по направлениям» (ключи приходят из
  `/dashboard/summary`);
- появляется в виджете метрик ТЗ (строка на каждый элемент `/metrics/models`);
- раскрашивается на карте по своему `accent`.

Если направление пришло в прогнозе, но его нет в `/meta`, `shared/lib/risk.ts`
синтезирует мету на лету: подпись = код, цвет = детерминированный хеш кода.
Экран не ломается даже при рассинхроне.

Проверяется тестом `src/mocks/flexibility.test.tsx`: он включает пятое направление
в моках и проверяет, что оно доехало до фильтров и дашборда.

Что нужно сделать в заглушках, чтобы направление появилось в демо, —
см. [04-mocks.md](04-mocks.md#добавить-направление).

---

## 1. Реестр блоков карточки

`src/card/blockRegistry.tsx`

Тело карточки прогноза приходит из API как `blocks: [{ type, title, data }]`.
Поле `data` намеренно `unknown` — форму знает только компонент блока.

```tsx
const registry: Record<string, BlockView> = {
  factors:    FactorsBlock,      // вклад признаков, полосы ±
  timeseries: TimeSeriesBlock,   // график показаний с отметкой момента прогноза
  timeline:   TimelineBlock,     // события объекта на оси времени
  table:      TableBlock,        // { columns, rows }
  keyvalue:   KeyValueBlock,     // список пар
}

export function BlockRenderer({ block }: { block: CardBlock }) {
  const View = registry[block.type] ?? GenericBlock
  return (
    <ErrorBoundary fallback={<BlockError title={block.title} />}>
      <View block={block} />
    </ErrorBoundary>
  )
}
```

### Добавить тип блока

1. `src/card/blocks/MyBlock.tsx` — компонент с пропом `{ block: CardBlock }`.
   Разбирай `block.data` через `safeParse` собственной схемы и при неудаче
   возвращай `<GenericBlock block={block} />`. Так блок деградирует, а не падает.
2. Одна строка в `registry`.
3. Тест: рендер с валидными данными и рендер с мусором.

До шага 1 блок уже отображается — `GenericBlock` рекурсивно рисует любой JSON
списком пар и таблицами для массивов объектов. Некрасиво, но информация видна.

`ErrorBoundary` вокруг каждого блока обязателен и уже есть в `BlockRenderer`.
Сломанные данные в одном блоке не уносят карточку.

---

## 2. Реестр действий

`src/card/actions/actionRegistry.tsx`

Действия приходят из API вместе с сущностью:

```ts
interface ActionDef {
  code: string
  label: string
  kind: 'primary' | 'secondary' | 'danger'
  confirm?: string
  fields: FieldDef[]
}
```

По умолчанию действие рисуется кнопкой, которая раскрывает `DynamicForm` —
форму, собранную по `fields`, с валидацией через zod, построенной
`buildZodSchema()`. Отправка идёт в единый эндпоинт действий.

Реестр нужен **только** когда действию нужен нестандартный UI:

```tsx
const registry: Record<string, ActionForm> = {
  close: CloseOrderForm,   // закрытие заявки: обязательная отметка
}                          // «прогноз подтвердился / не подтвердился»

const Form = registry[action.code] ?? DynamicForm
```

### Добавить действие

Обычное действие добавляется **на бэкенде**: положить его в `actions` сущности.
Фронт нарисует кнопку и форму сам. Ничего править не надо.

Нестандартный UI: компонент с пропами `{ action, meta, defaultValues, onSubmit }`
плюс строка в реестре.

### Типы полей формы

`src/card/actions/FieldRenderer.tsx`

| `type` | Контрол | Примечание |
|---|---|---|
| `text` | `<input>` | `minLength` уходит в zod |
| `textarea` | `<textarea>` | |
| `select` | `<select>` | опции берутся из `meta.reasons[field.optionsRef]` |
| `datetime` | `<input type="datetime-local">` | наружу уходит ISO |
| `boolean` | чекбокс | |

Неизвестный `type` рисуется текстовым полем — форма остаётся отправляемой.

Справочник причин меняется на бэкенде в `meta.reasons`, фронт подхватывает без
пересборки.

---

## 3. Реестр колонок

`src/table/columnRegistry.tsx`

Состав и порядок колонок журнала — из `meta.journalColumns`, запасной список —
`FALLBACK_META.journalColumns`.

```ts
interface ColumnDef<Row> {
  key: string
  header: string
  width?: number
  align?: 'left' | 'right'
  sortable?: boolean
  cell: (row: Row, meta: AppMeta) => React.ReactNode
}

export function resolveColumns<Row>(
  keys: string[],
  registry: Record<string, ColumnDef<Row>>,
): ColumnDef<Row>[]     // неизвестные ключи молча отбрасываются
```

Два реестра: `predictionColumns` для журнала и `orderColumns` для заявок.

### Добавить колонку

1. Строка в нужный реестр.
2. Ключ в `meta.journalColumns` (в моках — `src/mocks/db/meta.ts`).

Если ключ есть в мете, но нет в реестре, — колонка просто не рисуется. Это
нормальный режим на время, пока фронт догоняет бэкенд.

---

## 4. Реестр виджетов дашборда

`src/widgets/widgetRegistry.tsx`

```ts
const registry: Record<string, React.FC> = {
  'risk-counters':   RiskCounters,     // по уровням риска
  'direction-split': DirectionSplit,   // по направлениям, из реестра
  'top-risks':       TopRisks,         // топ-10 объектов
  'model-metrics':   ModelMetrics,     // Precision/Recall против целевых 0.7 / 0.5
  'pipeline-health': PipelineHealth,   // время расчёта против 5 мин, свежесть
  'order-counters':  OrderCounters,    // заявки по статусам
}
```

Состав и порядок — из `meta.dashboardWidgets`. Неизвестный код рисуется
`GenericWidget` — панелью с кодом и подписью «виджет не реализован». Дашборд не
падает и честно сообщает, чего не хватает.

Каждый виджет сам грузит свои данные через хук. Дашборд не собирает данные и не
раздаёт их сверху — иначе добавление виджета потребовало бы правки дашборда.

### Добавить виджет

1. Компонент в `src/widgets/`, без пропов, данные — своим хуком.
2. Строка в реестре.
3. Код в `meta.dashboardWidgets`.

---

## Флаги экранов

`src/shared/config/features.ts`

```ts
export const features = { dashboard: true, map: true, journal: true, orders: true }
```

Выключенный экран пропадает и из рельса, и из роутера. Нужно, если что-то не
успевается к защите: лучше четыре работающих пункта меню, чем пять, один из
которых белый экран.

---

## Что нельзя делать ни при каких обстоятельствах

- `type Direction = 'FIRE_RISK' | ...` — union по направлениям, уровням, статусам.
  Только `string` плюс реестр.
- `switch (prediction.direction)` где угодно, включая тернарники и словари цветов,
  захардкоженные по кодам.
- Отдельный компонент карточки под конкретное направление.
- Отдельный эндпоинт под конкретную кнопку.
- Бросок исключения при неизвестном поле, типе блока, коде действия или виджета.

Первые два пункта проверяются тестом `src/architecture.test.ts`, который читает
исходники и падает, если находит нарушение.
