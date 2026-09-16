# АРМ диспетчера ОДС — спецификация фронтенда, версия 2

Задание для Claude Code. Только фронтенд, полностью на моках до появления бэкенда.
Периметр сокращён до того, что прямо перечислено в описании итогового продукта.

---

## 1. Периметр

Из ТЗ: «веб-интерфейс диспетчера ОДС с дашбордом рисков, картой объектов и журналом
прогнозов + модуль автоматического формирования заявок на превентивное обслуживание».
Отсюда ровно четыре экрана и одна правая панель.

| Экран | Маршрут | Зачем |
|---|---|---|
| Дашборд рисков | `/` | Где горит + доказательство метрик ТЗ |
| Карта объектов | `/map` | Пространственная картина риска |
| Журнал прогнозов | `/journal` | Основная рабочая таблица |
| Заявки | `/orders` | Что система создала и что с этим стало |
| Карточка прогноза | правая панель на `/map` и `/journal` | Решение |

**Явно вне периметра.** Не реализуем: звуковые оповещения, ленту событий, смены и
приёмку/сдачу, планирование ППР, календарь, мобильный клиент бригады, режим
видеостены, канбан, командную палитру, переключатель масштаба, отсрочку прогноза,
раздел аналитики.

Мобильную версию заказчик убрал из минимальных требований сам. Тёмную тему ТЗ не
требует, но она у нас есть и даёт плюс при прочих равных.

**Вернулось в периметр после QA-сессии.** Два пункта, которые прежняя версия
относила к лишнему.

1. **Вход и роли.** ТЗ §11 держит RBAC и федерацию с LDAP/AD в обязательных
   требованиях. Невыполненное обязательное требование без объяснения снимает
   решение с оценки по формальному признаку. Ролей четыре, они описаны в
   `ARM-ODS-backend-spec.md` §10.
2. **Уведомления о критических инцидентах.** ТЗ §10 требует оповещать в режиме
   реального времени. Заказчик снизил планку: уведомления внутри веб-интерфейса
   достаточно, почта и Telegram приветствуются, но обязательными не являются.

Обоснование для опроса раз в минуту остаётся для прогнозов: минимальный горизонт
по ТЗ 24 часа, и событие, до которого сутки, не требует реакции за секунды.
Уведомления живут отдельным каналом и на этот опрос не опираются.

---

## 2. Главный архитектурный принцип

**Фронтенд не знает, сколько направлений прогнозирования существует и как они
называются.** Он получает описание с бэкенда и строит интерфейс по нему. ТЗ может
измениться — добавится направление, поменяется набор полей карточки, изменится список
действий — и это не должно приводить к правкам в экранах.

Четыре следствия, которым нужно следовать без исключений:

1. **`Direction` — это `string`, а не union.** Никогда не пишите
   `type Direction = 'FIRE_RISK' | 'SENSOR_FAILURE' | ...`. Как только напишете,
   компилятор потребует обрабатывать каждый вариант в каждом `switch`, и новое
   направление станет рефакторингом на полдня.
2. **Тело карточки — массив блоков, а не вёрстка.** Бэкенд отдаёт
   `blocks: [{ type, title, data }]`. Фронт держит реестр `type → компонент` и
   запасной рендерер для неизвестных типов.
3. **Действия приходят из API и ходят в один эндпоинт.**
   `POST /predictions/{id}/actions/{code}`. Не четыре ручки под четыре кнопки.
4. **Неизвестное игнорируется, а не роняет экран.** Разбор ответов через
   `zod` с `.passthrough()` и `safeParse`. Лишнее поле в ответе — не ошибка.

---

## 3. Стек

Минимальный набор, каждая позиция обоснована.

- Vite + React 18 + TypeScript strict
- React Router v6 — маршруты и, что важнее, **всё состояние UI в параметрах URL**
  (фильтры, сортировка, выбранный прогноз). Глобального стора нет вообще: ни Zustand,
  ни Redux. Ссылку на отфильтрованный журнал можно передать коллеге, кнопка «назад»
  работает правильно, состояние не нужно синхронизировать.
- TanStack Query v5 — серверные данные, кэш, опрос (`refetchInterval: 60_000`)
- Tailwind CSS + CSS-переменные для токенов
- MapLibre GL JS — карта
- Recharts — два-три мини-графика на дашборде и в карточке
- zod + react-hook-form — разбор ответов и динамические формы действий
- MSW + `@faker-js/faker` — моки
- Vitest + Testing Library

Без SSR, без shadcn/ui (нужно четыре примитива, проще написать), без виртуализации
таблиц — пагинация серверная по 50 строк.

---

## 4. Дизайн

Диспетчерская, приглушённый свет, плотная таблица. Тёмная тема — основная и
включена по умолчанию. Светлая добавлена как второй равноправный режим,
переключается кнопкой внизу рельса (ADR 0009); имена и смысл токенов у обеих
тем общие, компонент активную тему не знает и знать не должен.

Тёмная палитра:

```
--bg          #161C22   фон приложения
--panel       #1D242B   панели
--raised      #252E36   строка при наведении, вложенные блоки
--sunken      #10151A   поля ввода
--line        #2E3841   разделители
--line-strong #3E4A55   границы активных элементов
--text        #E4E9ED
--text-dim    #9AA8B4
--text-mute   #6B7883
```

Шкала риска — единственный смысловой цвет, непрерывный ход от холодного к горячему:

```
--risk-low       #3E8C74
--risk-medium    #C89230
--risk-high      #D4602F
--risk-critical  #C43B36
```

Уровень кодируется **дважды**: цветом и вертикальной планкой 3px слева от строки.
Только цветом нельзя.

Шрифты: `Golos Text` для интерфейса (Google Fonts, родная кириллица),
`JetBrains Mono` с `tabular-nums` для идентификаторов, времени и чисел в таблице.
Веса только 400 и 500. Размеры: 18/14/13/12. Сентенс-кейс везде.

`border-radius: 4px` на панелях и контролах, 0 на строках таблицы. Теней нет —
глубина передаётся фоном. Анимация только в ответ на действие, 120–160 мс.

Не делать: одинаковые скруглённые карточки с мягкой тенью на всё, ALL-CAPS подписи
над заголовками, градиенты, «большая цифра + мелкая подпись» как герой экрана,
стрелки `→` в тексте кнопок.

Каркас:

```
┌──────────────────────────────────────────────────────────┐
│ Шапка 48px: название · свежесть данных · ссылка на API   │
├────┬─────────────────────────────────┬───────────────────┤
│ Р  │                                 │  Правая панель    │
│ е  │      Рабочая зона               │  420px            │
│ л  │      (по маршруту)              │  карточка         │
│ ь  │                                 │  прогноза         │
│ с  │                                 │  (сворачивается)  │
└────┴─────────────────────────────────┴───────────────────┘
```

Рельс — четыре раздела, у каждого иконка и подпись под ней. Правая панель не
модальная: карта остаётся видна.

---

## 5. Структура проекта

```
src/
  app/
    router.tsx
    providers.tsx
    AppShell.tsx        шапка, рельс, зоны, правая панель
  screens/
    dashboard/
    map/
    journal/
    orders/
  card/                 карточка прогноза — общий модуль для map и journal
    PredictionCard.tsx
    blockRegistry.tsx   ← точка расширения №1
    blocks/             FactorsBlock, TimeSeriesBlock, TimelineBlock,
                        TableBlock, KeyValueBlock, GenericBlock
    actionRegistry.tsx  ← точка расширения №2
    DynamicForm.tsx
  table/
    DataTable.tsx
    columnRegistry.tsx  ← точка расширения №3
  widgets/
    widgetRegistry.tsx  ← точка расширения №4 (дашборд)
  shared/
    api/                http.ts, schemas.ts, endpoints.ts
    ui/                 Panel, Button, Badge, RiskBar, Field, EmptyState, ErrorState
    lib/                format.ts, urlState.ts, risk.ts
    config/             tokens.css, features.ts, fallbacks.ts
  mocks/
    handlers.ts, seed.ts, browser.ts
```

Плоско: пять папок вместо трёхуровневой FSD. На проекте этого размера слои дают
больше церемонии, чем пользы.

---

## 6. Типы

Обратите внимание, чего здесь нет: перечислений направлений и жёстких форм блоков.

```ts
// Реестр, приходящий с бэкенда. Всё, что фронт знает о предметной области.
export interface DirectionMeta {
  code: string;              // строка, не union
  label: string;
  shortLabel: string;
  accent: string;            // hex
  minHorizonHours: number;
}

export interface RiskLevelMeta {
  code: string;              // 'LOW' | 'MEDIUM' | ... но типом — строка
  label: string;
  colorVar: string;          // '--risk-high'
  order: number;
}

export interface AppMeta {
  directions: DirectionMeta[];
  riskLevels: RiskLevelMeta[];
  journalColumns: string[];        // ключи колонок в нужном порядке
  dashboardWidgets: string[];      // коды виджетов в нужном порядке
  reasons: Record<string, { code: string; label: string }[]>;  // справочники причин
}

// Единица прогноза: пара «объект и пикет». Пикет равен 10 метрам и является
// контейнером оборудования. Таких пар 3 783.
export interface FacilityRef {
  id: string;
  complex: string;           // комплекс, 16 штук: Альфа, Бета, Гамма, …
  object: string;            // объект внутри комплекса, 78 штук
  picket: number;            // номер пикета
  offsetM?: number;          // смещение внутри пикета, метры
  /** координата вдоль трассы: picket * 10 + offsetM */
  distanceM: number;
  label: string;             // подпись для человека
  lat: number;               // точка на синтетической трассе
  lon: number;
}

// Блок карточки — намеренно нетипизированное data
export interface CardBlock {
  type: string;              // 'factors' | 'timeseries' | ... и что угодно ещё
  title: string;
  data: unknown;
}

// Описание поля формы действия
export type FieldDef =
  | { name: string; label: string; type: 'select'; required?: boolean; optionsRef: string }
  | { name: string; label: string; type: 'text' | 'textarea'; required?: boolean; minLength?: number }
  | { name: string; label: string; type: 'datetime'; required?: boolean }
  | { name: string; label: string; type: 'boolean'; required?: boolean };

export interface ActionDef {
  code: string;
  label: string;
  kind: 'primary' | 'secondary' | 'danger';
  confirm?: string;          // текст подтверждения, если нужно
  fields: FieldDef[];
}

export interface Prediction {
  id: string;
  direction: string;         // код из реестра
  level: string;             // код из реестра
  probability: number;       // 0..1
  horizonHours: number;      // >= 24 по ТЗ
  computedAt: string;        // ISO — время формирования прогноза
  computeMs: number;         // сколько считался, для метрики < 5 мин
  status: string;            // NEW | IN_REVIEW | ORDER_CONFIRMED | REJECTED | CLOSED
  facility: FacilityRef;
  summary: string;           // одна строка человеческим языком
  orderId?: string;          // автоматически созданная заявка
}

export interface PredictionDetail extends Prediction {
  blocks: CardBlock[];
  actions: ActionDef[];
}

export interface WorkOrder {
  id: string;
  number: string;
  predictionId: string;
  facility: FacilityRef;
  workType: string;
  dueAt: string;
  status: string;            // AUTO_CREATED | CONFIRMED | REJECTED | IN_PROGRESS | DONE
  createdAt: string;
  actions: ActionDef[];
  outcome?: {
    actualCause: string;
    predictionConfirmed: boolean;
    comment: string;
    closedAt: string;
  };
}

export interface ModelMetric {
  direction: string;
  precision: number;
  recall: number;
  targetPrecision: number;   // 0.7
  targetRecall: number;      // 0.5
  evaluatedAt: string;
}
```

---

## 7. API

База `/api/v1`. Списки — `{ items, page, pageSize, total }`.

```
GET  /meta                                   → AppMeta   (грузится один раз при старте)

GET  /predictions?direction&level&status&complex&from&to&sort&page&pageSize
GET  /predictions/{id}                       → PredictionDetail
POST /predictions/{id}/actions/{code}        тело = значения полей формы
GET  /predictions/{id}/timeseries            ряд для графика в карточке

GET  /facilities?bbox&direction&level&complex  → GeoJSON FeatureCollection
GET  /facilities/lines                       трассы коллекторов, грузятся один раз
GET  /facilities/{id}

GET  /orders?status&dueBefore&sort&page&pageSize
GET  /orders/{id}
POST /orders/{id}/actions/{code}

GET  /metrics/models                         → ModelMetric[]
GET  /metrics/pipeline                       → { lastRunAt, lastRunMs, freshnessMinutes,
                                                 maxComputeMs, minHorizonHours,
                                                 targetComputeMs, targetHorizonHours }
GET  /dashboard/summary                      → счётчики по уровням, направлениям,
                                                 статусам прогнозов и статусам заявок
GET  /dashboard/top-risks?limit=10           → Prediction[]
```

Окно графика выбирает сервер: у таймсерий параметров нет. Трассы коллекторов
живут на своей ручке, потому что геометрия сети не меняется, а точки на карте
опрашиваются раз в минуту. Границы `bbox` карта шлёт после остановки, округляя
их до 0.05°.

Один эндпоинт действий — ключевое решение. Форма отправляется как есть, бэкенд знает,
что делать с кодом. Ответ — обновлённый `PredictionDetail`, чтобы фронт не гадал,
что изменилось.

---

## 8. Точки расширения

Это то, что нужно написать в первую очередь. Всё остальное строится поверх.

### 8.1 Реестр блоков карточки

```tsx
// card/blockRegistry.tsx
import type { CardBlock } from '@/shared/api/schemas';

type BlockView = React.FC<{ block: CardBlock }>;

const registry: Record<string, BlockView> = {
  factors: FactorsBlock,        // вклад признаков, горизонтальные полосы ±
  timeseries: TimeSeriesBlock,  // график показаний с отметкой момента прогноза
  timeline: TimelineBlock,      // события объекта на оси времени
  table: TableBlock,            // произвольная таблица {columns, rows}
  keyvalue: KeyValueBlock,      // список пар
};

export function BlockRenderer({ block }: { block: CardBlock }) {
  const View = registry[block.type] ?? GenericBlock;
  return (
    <ErrorBoundary fallback={<BlockError title={block.title} />}>
      <View block={block} />
    </ErrorBoundary>
  );
}
```

`GenericBlock` рекурсивно рисует `data` как список пар «ключ — значение» и таблицы
для массивов объектов. Он должен работать на любом JSON. `ErrorBoundary` вокруг
каждого блока обязателен: сломанные данные в одном блоке не должны уносить карточку.

Добавление нового типа блока = один файл + одна строка в реестре. До этого момента
блок уже отображается, просто некрасиво.

### 8.2 Реестр действий и динамическая форма

```tsx
// card/DynamicForm.tsx — строит форму по FieldDef[]
export function DynamicForm({
  action, meta, onSubmit,
}: { action: ActionDef; meta: AppMeta; onSubmit: (v: Record<string, unknown>) => void }) {
  const schema = buildZodSchema(action.fields);   // required, minLength → zod
  const form = useForm({ resolver: zodResolver(schema) });
  return (
    <div>
      {action.fields.map(f => <FieldRenderer key={f.name} field={f} form={form} meta={meta} />)}
      <Button kind={action.kind} onClick={form.handleSubmit(onSubmit)}>{action.label}</Button>
    </div>
  );
}
```

`optionsRef` в поле типа `select` — ключ в `meta.reasons`. Справочник причин меняется
на бэкенде, фронт подхватывает.

`actionRegistry` нужен только для случаев, когда действие требует нестандартного UI
(например, закрытие заявки с обязательной отметкой «подтвердился»). Логика та же:
`registry[action.code] ?? DynamicForm`.

### 8.3 Реестр колонок

```ts
// table/columnRegistry.tsx
export interface ColumnDef {
  key: string;
  header: string;
  width?: number;
  align?: 'left' | 'right';
  cell: (row: Prediction, meta: AppMeta) => React.ReactNode;
}

export const columnRegistry: Record<string, ColumnDef> = {
  risk:        { key: 'risk', header: '', width: 4, cell: r => <RiskBar level={r.level} /> },
  computedAt:  { key: 'computedAt', header: 'Рассчитан', cell: r => <Mono>{fmtDateTime(r.computedAt)}</Mono> },
  direction:   { key: 'direction', header: 'Направление', cell: (r, m) => directionLabel(r.direction, m) },
  facility:    { key: 'facility', header: 'Объект', cell: r => <FacilityCell value={r.facility} /> },
  summary:     { key: 'summary', header: 'Прогноз', cell: r => r.summary },
  probability: { key: 'probability', header: 'Вероятность', align: 'right', cell: r => fmtPct(r.probability) },
  horizon:     { key: 'horizon', header: 'Горизонт', align: 'right', cell: r => `${r.horizonHours} ч` },
  status:      { key: 'status', header: 'Статус', cell: r => <StatusBadge code={r.status} /> },
  order:       { key: 'order', header: 'Заявка', cell: r => r.orderId ? <OrderLink id={r.orderId} /> : '—' },
};

export function resolveColumns(keys: string[]): ColumnDef[] {
  return keys.map(k => columnRegistry[k]).filter(Boolean);
}
```

Порядок и состав колонок — из `meta.journalColumns`, с запасным списком в
`shared/config/fallbacks.ts` на случай, если `/meta` недоступен.

### 8.4 Реестр виджетов дашборда

```ts
export const widgetRegistry: Record<string, React.FC> = {
  'risk-counters': RiskCounters,       // по уровням
  'direction-split': DirectionSplit,   // по направлениям, из реестра
  'top-risks': TopRisks,               // топ-10 объектов
  'model-metrics': ModelMetrics,       // Precision/Recall против целевых
  'pipeline-health': PipelineHealth,   // время расчёта против 5 минут, свежесть
  'order-counters': OrderCounters,
};
```

Состав и порядок — из `meta.dashboardWidgets`.

### 8.5 Слой изоляции от контракта

Весь HTTP — в `shared/api/http.ts`. Все схемы — в `shared/api/schemas.ts`, каждая через
`z.object({...}).passthrough()` и `safeParse`. Если разбор не прошёл — пишем предупреждение
в консоль и возвращаем данные как есть, а не бросаем исключение. На хакатоне бэкенд
меняется каждые два часа, и фронт не должен падать из-за переименованного поля.

Флаги в `shared/config/features.ts`: `{ dashboard, map, journal, orders }`. Выключенный
экран пропадает из рельса и из роутера. Пригодится, если что-то не успеваете.

---

## 9. Экраны

### Дашборд `/`

Плотная сетка из виджетов, порядок из `meta.dashboardWidgets`. Обязательные:

- Счётчики по уровням риска — числа с цветными планками, не «большие цифры».
- Разбивка по направлениям — горизонтальные полосы, подписи из реестра.
- Топ-10 объектов риска — компактная таблица, клик открывает карточку.
- **Соответствие метрикам ТЗ** — по строке на направление: Precision и Recall
  рядом с целевыми 0.7 и 0.5, зелёная или красная отметка. Отдельно время последнего
  расчёта против 5 минут и минимальный горизонт против 24 часов. Этот виджет
  существует, чтобы жюри увидело выполнение метрик, не открывая ноутбук с моделью.
- Счётчики заявок по статусам.

### Карта `/map`

MapLibre, тёмная подложка. Трассы коллекторов ломаными, точки единиц прогноза на
них, цвет — уровень риска, кластеризация. Фильтры по направлению и уровню — те же
параметры URL, что и в журнале. Легенда внизу слева. Клик по точке ставит
`?prediction=<id>` и открывает правую панель.

Настоящих координат нет и не будет: заказчик использует московскую систему
координат, а пересчёт в общедоступные карты выходит за рамки проекта. Он прямо
разрешил сгенерировать линейные объекты самим.

Отсюда два правила карты.

1. **Длина линии настоящая, положение условное.** Точка ставится по координате
   вдоль трассы, `picket * 10 + offsetM`. Объект `Кси ПК0-ПК202` даёт линию в
   2 020 метров, а не произвольный отрезок.
2. **Карта не притворяется картой города.** Подпись «схема, координаты условные»
   стоит на экране постоянно, а не в подсказке. Девять округов Москвы из прежних
   заглушек убраны: в данных один район и 16 комплексов.

### Журнал `/journal`

`DataTable` с колонками из реестра, серверная пагинация по 50, сортировка по
клику на заголовок. Фильтры — панель над таблицей, состояние в URL. Кнопка выгрузки
текущей выборки в CSV. Строка кликом ставит `?prediction=<id>`.

### Карточка прогноза (правая панель)

Шапка: направление и уровень, объект с иерархией, вероятность, горизонт в часах,
время расчёта. Если есть `orderId` — компактная плашка заявки со ссылкой.

Тело: `blocks.map(b => <BlockRenderer block={b} />)`. Никакой собственной вёрстки
под конкретные направления.

Низ, липкая панель: кнопки из `actions`. Нажатие раскрывает форму по `fields`.
После успеха — карточка перерисовывается ответом сервера, панель остаётся открытой,
чтобы диспетчер увидел новый статус.

### Заявки `/orders`

Таблица: номер, объект, вид работ, срок, статус, связанный прогноз. Фильтр по статусу.
Карточка заявки в правой панели: те же `actions` и `DynamicForm`.

Закрытие заявки — единственное действие с фиксированной формой: фактическая причина
из справочника, обязательный радиовыбор «прогноз подтвердился / не подтвердился»,
комментарий. Отметка подтверждения — то, из чего считается качество на реальных данных.

---

## 10. Моки

`VITE_USE_MOCKS=true` — приложение полностью работает без бэкенда.

Детерминированный сид повторяет форму настоящей выгрузки: 16 комплексов, 78
объектов, единицы прогноза как пары «объект и пикет». Координаты — синтетические
полилинии, длина каждой считается из номеров пикетов (`picket * 10`), положение в
городе условное. 260 прогнозов с распределением по направлениям и уровням, у всех
`horizonHours >= 24`. 90 заявок во всех статусах, часть связана с прогнозами.
Метрики моделей — правдоподобные значения, у двух направлений выше целевых, у
одного на грани (чтобы виджет метрик было видно в обоих состояниях).

`/meta` в моках должен отдавать все направления, список комплексов, состав
колонок и виджетов.
Проверка гибкости: добавьте в мок новое направление и убедитесь, что фильтры, карта
и дашборд подхватили его без правок кода. Это отдельный тест.

Задержка 150–500 мс. Кнопка в dev-панели: «сломать `/meta`» — приложение должно
подняться на запасных конфигах.

---

## 11. Порядок сборки

1. Каркас: Vite, TS strict, Tailwind, токены, `AppShell`, роутер, четыре заглушки.
2. `shared/api`: http-клиент, схемы с `passthrough`, `/meta` и хук `useMeta()`,
   запасные конфиги.
3. Моки: сид, все GET-хендлеры, `/meta`, dev-панель.
4. `shared/ui`: Panel, Button, Badge, RiskBar, Field, EmptyState, ErrorState.
5. `DataTable` + `columnRegistry` + состояние фильтров в URL.
6. Журнал целиком.
7. Карточка: `blockRegistry`, все шесть блоков, `GenericBlock`, `ErrorBoundary`.
8. `DynamicForm` + отправка действий в единый эндпоинт.
9. Карта, синхронизация фильтров и выбора с журналом.
10. Дашборд: `widgetRegistry`, все шесть виджетов, виджет метрик ТЗ.
11. Заявки и закрытие с разметкой.
12. Опрос раз в минуту, состояния ошибок и пустоты, доступность с клавиатуры.

---

## 12. Критерии приёмки

- Работает целиком на моках, без бэкенда.
- Добавление нового направления в мок `/meta` не требует правок кода: оно появляется
  в фильтрах журнала, на карте и на дашборде.
- Блок неизвестного типа в карточке рисуется запасным рендерером, экран не падает.
- Действие с неизвестным кодом рисуется обычной кнопкой с формой по описанию полей.
- Поломанный `/meta` не мешает приложению подняться на запасных конфигах.
- Ссылка на отфильтрованный журнал с открытой карточкой восстанавливает то же состояние.
- Виджет метрик показывает Precision и Recall против 0.7 и 0.5, время расчёта против
  5 минут и минимальный горизонт против 24 часов.
- Полный цикл «прогноз → подтверждение заявки → закрытие с разметкой» проходится
  с клавиатуры.
- `tsc --noEmit` и `eslint` чисты. Ни одного `switch` по коду направления во всём коде.

---

## 13. Чего не делать

- Не заводить union-тип для направлений, уровней и статусов. Строки плюс реестр.
- Не писать отдельные компоненты карточки под каждое направление.
- Не заводить отдельные эндпоинты под каждую кнопку.
- Не добавлять глобальный стор: состояние живёт в URL и в кэше TanStack Query.
- Не добавлять WebSocket. Горизонт 24 часа этого не требует.
- Не бросать исключение при неизвестном поле в ответе API.
- Не расширять периметр за пределы четырёх экранов, пока эти четыре не закончены.
