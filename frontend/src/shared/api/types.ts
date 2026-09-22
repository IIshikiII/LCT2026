/**
 * Типы предметной области. Соответствуют spec §6.
 *
 * ВАЖНО (ADR 0002): `direction`, `level`, `status` — это `string`, а не union.
 * Как только появится union, компилятор потребует обрабатывать каждый вариант в
 * каждом ветвлении, и новое направление станет рефакторингом на полдня.
 *
 * ВАЖНО (ADR 0006): эти типы описывают ожидания, а не гарантии. Разбор ответов
 * не бросает исключений, поэтому во время выполнения любое поле может оказаться
 * `undefined`. Форматтеры в shared/lib/format.ts принимают `unknown` именно
 * поэтому.
 */

/** Направление прогнозирования. Всё, что фронт о нём знает, приходит из /meta. */
export interface DirectionMeta {
  code: string
  label: string
  shortLabel: string
  /** hex-цвет для карты и разбивки по направлениям */
  accent: string
  /** минимальный горизонт прогноза, по ТЗ не меньше 24 */
  minHorizonHours: number
}

/** Уровень риска. Порядок задаётся полем order, а не позицией в массиве. */
export interface RiskLevelMeta {
  code: string
  label: string
  /** имя CSS-переменной, например '--risk-high' */
  colorVar: string
  order: number
}

/** Подпись статуса. scope разделяет статусы прогнозов и заявок. */
export interface StatusMeta {
  code: string
  label: string
  /** 'prediction' | 'order' — но типом строка, чтобы бэкенд мог добавить свой */
  scope: string
  /**
   * Цвет метки статуса: имя токена вида '--state-progress' либо готовый цвет.
   * Как и у уровней риска, цвет назначает бэкенд — фронт не знает кодов
   * статусов и не ветвится по ним (ADR 0002, ADR 0010). Нет значения —
   * метка остаётся нейтральной.
   */
  colorVar?: string
  /**
   * Конечный статус: работа по сущности закончена. Из него интерфейс узнаёт,
   * что просрочка уже неважна, а действий больше не будет.
   */
  terminal?: boolean
}

export interface ReasonOption {
  code: string
  label: string
}

export interface DistrictMeta {
  code: string
  label: string
}

/** Ответ GET /meta — описание всей предметной области. */
export interface AppMeta {
  directions: DirectionMeta[]
  riskLevels: RiskLevelMeta[]
  statuses: StatusMeta[]
  districts: DistrictMeta[]
  /** ключи колонок журнала в нужном порядке */
  journalColumns: string[]
  /** ключи колонок экрана заявок в нужном порядке */
  orderColumns: string[]
  /** коды виджетов дашборда в нужном порядке */
  dashboardWidgets: string[]
  /** справочники для полей формы типа select; ключ = FieldDef.optionsRef */
  reasons: Record<string, ReasonOption[]>
}

/** Объект инфраструктуры: датчик, камера, вентшахта, насос, люк, участок. */
export interface FacilityRef {
  id: string
  collector: string
  section?: string
  chamber?: string
  device?: string
  district: string
  address: string
  lat: number
  lon: number
}

/**
 * Блок карточки. `data` намеренно не типизирован: его форму знает только
 * компонент блока (ADR 0003). Неизвестный `type` рисуется GenericBlock.
 */
export interface CardBlock {
  type: string
  title: string
  data: unknown
}

/**
 * Описание поля формы действия.
 *
 * `type` — строка, а не union: неизвестный тип поля деградирует до текстового
 * ввода, а не ломает форму.
 */
export interface FieldDef {
  name: string
  label: string
  /** 'text' | 'textarea' | 'select' | 'datetime' | 'boolean' | что угодно ещё */
  type: string
  required?: boolean
  minLength?: number
  /** для type='select': ключ справочника в meta.reasons */
  optionsRef?: string
  placeholder?: string
  help?: string
}

/** Действие над сущностью. Уходит в единый эндпоинт действий (ADR 0004). */
export interface ActionDef {
  code: string
  label: string
  /** 'primary' | 'secondary' | 'danger' — строкой, неизвестное = secondary */
  kind: string
  /** текст подтверждения, если действие требует «вы уверены» */
  confirm?: string
  /** подсказка под кнопкой: контекст, который знает только сервер */
  help?: string
  fields: FieldDef[]
}

export interface Prediction {
  id: string
  direction: string
  level: string
  /** 0..1 */
  probability: number
  /** по ТЗ >= 24 */
  horizonHours: number
  /** ISO — момент формирования прогноза */
  computedAt: string
  /** сколько миллисекунд считался, для метрики ТЗ «< 5 минут» */
  computeMs: number
  status: string
  facility: FacilityRef
  /** одна строка человеческим языком */
  summary: string
  /** автоматически созданная заявка, если она есть */
  orderId?: string
  /** кто взял прогноз в работу */
  assignee?: string
  /** 'AGREED' | 'CORRECTED' — согласился диспетчер с уровнем или исправил */
  verdict?: string
  /** уровень, который диспетчер считает верным */
  dispatcherLevel?: string
  /** ISO — когда диспетчер принял решение */
  decidedAt?: string
  /** итог бригады: подтверждён ли факт на объекте */
  factConfirmed?: boolean
}

export interface PredictionDetail extends Prediction {
  blocks: CardBlock[]
  actions: ActionDef[]
}

export interface WorkOrderOutcome {
  actualCause: string
  /**
   * Факт на объекте по итогу выезда. Второй ярлык для дообучения (ADR 0006).
   * Бригада видела факт, а не пользу выезда: пользу измерить нечем.
   */
  factConfirmed: boolean
  comment: string
  closedAt: string
}

export interface WorkOrder {
  id: string
  number: string
  /** 'PIPELINE' | 'DISPATCHER' — кто породил заявку */
  createdBy?: string
  predictionId: string
  facility: FacilityRef
  workType: string
  dueAt: string
  status: string
  createdAt: string
  actions: ActionDef[]
  outcome?: WorkOrderOutcome
}

export interface ModelMetric {
  direction: string
  precision: number
  recall: number
  /** цель по ТЗ: 0.7 */
  targetPrecision: number
  /** цель по ТЗ: 0.5 */
  targetRecall: number
  evaluatedAt: string
}

/** Состояние конвейера расчёта — доказательство метрик «< 5 мин» и «>= 24 ч». */
export interface PipelineHealth {
  lastRunAt: string
  lastRunMs: number
  freshnessMinutes: number
  /** максимальное время расчёта одного прогноза за прогон, мс */
  maxComputeMs: number
  /** минимальный горизонт среди актуальных прогнозов, часы */
  minHorizonHours: number
  /** целевые значения по ТЗ, приходят с бэкенда, чтобы не хардкодить на фронте */
  targetComputeMs: number
  targetHorizonHours: number
}

/**
 * Счётчики дашборда. Все разбивки — Record<string, number>, а не поля с
 * фиксированными именами: появилось направление — появился ключ.
 */
export interface DashboardSummary {
  byLevel: Record<string, number>
  byDirection: Record<string, number>
  byStatus: Record<string, number>
  byOrderStatus: Record<string, number>
  total: number
}

export interface SeriesPoint {
  /** ISO */
  t: string
  v: number
}

export interface Series {
  name: string
  unit?: string
  points: SeriesPoint[]
}

export interface TimeSeriesResponse {
  series: Series[]
  /** момент формирования прогноза — вертикальная отметка на графике */
  markerAt?: string
}

/** Конверт списка. Одинаков для всех списочных ручек. */
export interface PageResult<T> {
  items: T[]
  page: number
  pageSize: number
  total: number
}

/** Точка на карте. properties плоские — MapLibre не умеет вложенные. */
export interface FacilityFeature {
  type: 'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] }
  properties: {
    facilityId: string
    predictionId?: string
    direction?: string
    level?: string
    probability?: number
    address?: string
    collector?: string
  }
}

export interface FacilityCollection {
  type: 'FeatureCollection'
  features: FacilityFeature[]
}

/**
 * Трассы коллекторов — подложка офлайн-стиля карты (ADR 0008).
 *
 * Приходят отдельной ручкой, а не вместе с точками: геометрия сети не меняется,
 * а точки опрашиваются раз в минуту.
 */
export interface LineCollection {
  type: 'FeatureCollection'
  features: {
    type: 'Feature'
    geometry: { type: 'LineString'; coordinates: [number, number][] }
    properties: { collector: string }
  }[]
}
