/**
 * zod-схемы ответов API.
 *
 * Все объекты — «мягкие» (`z.looseObject`, в zod 3 это называлось
 * `.passthrough()`): лишнее поле в ответе сохраняется, а не отбрасывается и не
 * считается ошибкой. Разбор идёт через `parseTolerant` и никогда не бросает —
 * см. ADR 0006 и docs/02-api-contract.md.
 *
 * Схема здесь — описание ожиданий и точка наблюдения за расхождением с
 * бэкендом, а не охранник на входе. Типы результата берутся из ./types.
 */
import { z } from 'zod'

/** Мягкий объект. Псевдоним нужен, чтобы правило читалось в каждой схеме. */
const dto = z.looseObject

/**
 * Необязательное поле: отсутствие ключа и `null` значат одно и то же.
 *
 * Бэкенд на FastAPI отдаёт пустое значение как `null`, а не пропускает ключ:
 * `"orderId": null`, `"device": null`, `"terminal": null`. Голый `.optional()`
 * такой ответ отвергает, и разбор всей карточки уходит в запасной путь
 * `parseTolerant`. Схема тогда перестаёт быть точкой наблюдения: она ругается
 * на каждый ответ. Поэтому `null` принимается и приводится к `undefined` —
 * типы в ./types.ts обещают именно `undefined`.
 */
function opt<T extends z.ZodType>(schema: T) {
  return schema.nullish().transform((value) => value ?? undefined)
}

export const DirectionMetaSchema = dto({
  code: z.string(),
  label: z.string(),
  shortLabel: z.string(),
  accent: z.string(),
  minHorizonHours: z.number(),
})

export const RiskLevelMetaSchema = dto({
  code: z.string(),
  label: z.string(),
  colorVar: z.string(),
  order: z.number(),
})

export const StatusMetaSchema = dto({
  code: z.string(),
  label: z.string(),
  scope: z.string(),
  colorVar: opt(z.string()),
  terminal: opt(z.boolean()),
})

export const ReasonOptionSchema = dto({
  code: z.string(),
  label: z.string(),
})

export const DistrictMetaSchema = dto({
  code: z.string(),
  label: z.string(),
})

export const AppMetaSchema = dto({
  directions: z.array(DirectionMetaSchema),
  riskLevels: z.array(RiskLevelMetaSchema),
  statuses: z.array(StatusMetaSchema),
  districts: z.array(DistrictMetaSchema),
  journalColumns: z.array(z.string()),
  orderColumns: z.array(z.string()),
  dashboardWidgets: z.array(z.string()),
  reasons: z.record(z.string(), z.array(ReasonOptionSchema)),
})

export const FacilityRefSchema = dto({
  id: z.string(),
  collector: z.string(),
  section: opt(z.string()),
  chamber: opt(z.string()),
  device: opt(z.string()),
  district: z.string(),
  address: z.string(),
  lat: z.number(),
  lon: z.number(),
})

/** data сознательно unknown: форму знает только компонент блока (ADR 0003). */
export const CardBlockSchema = dto({
  type: z.string(),
  title: z.string(),
  data: z.unknown(),
})

export const FieldDefSchema = dto({
  name: z.string(),
  label: z.string(),
  type: z.string(),
  required: opt(z.boolean()),
  minLength: opt(z.number()),
  optionsRef: opt(z.string()),
  placeholder: opt(z.string()),
  help: opt(z.string()),
})

export const ActionDefSchema = dto({
  code: z.string(),
  label: z.string(),
  kind: z.string(),
  confirm: opt(z.string()),
  help: opt(z.string()),
  fields: z.array(FieldDefSchema),
})

export const PredictionSchema = dto({
  id: z.string(),
  direction: z.string(),
  level: z.string(),
  probability: z.number(),
  horizonHours: z.number(),
  computedAt: z.string(),
  computeMs: z.number(),
  status: z.string(),
  facility: FacilityRefSchema,
  summary: z.string(),
  orderId: opt(z.string()),
  assignee: opt(z.string()),
  verdict: opt(z.string()),
  dispatcherLevel: opt(z.string()),
  decidedAt: opt(z.string()),
  factConfirmed: opt(z.boolean()),
})

export const PredictionDetailSchema = dto({
  ...PredictionSchema.shape,
  blocks: z.array(CardBlockSchema),
  actions: z.array(ActionDefSchema),
})

export const WorkOrderOutcomeSchema = dto({
  actualCause: z.string(),
  factConfirmed: z.boolean(),
  comment: z.string(),
  closedAt: z.string(),
})

export const WorkOrderSchema = dto({
  id: z.string(),
  createdBy: opt(z.string()),
  number: z.string(),
  predictionId: z.string(),
  facility: FacilityRefSchema,
  workType: z.string(),
  dueAt: z.string(),
  status: z.string(),
  createdAt: z.string(),
  actions: z.array(ActionDefSchema),
  outcome: opt(WorkOrderOutcomeSchema),
})

export const ModelMetricSchema = dto({
  direction: z.string(),
  precision: z.number(),
  recall: z.number(),
  targetPrecision: z.number(),
  targetRecall: z.number(),
  evaluatedAt: z.string(),
})

export const PipelineHealthSchema = dto({
  lastRunAt: z.string(),
  lastRunMs: z.number(),
  freshnessMinutes: z.number(),
  maxComputeMs: z.number(),
  minHorizonHours: z.number(),
  targetComputeMs: z.number(),
  targetHorizonHours: z.number(),
})

const counters = z.record(z.string(), z.number())

export const DashboardSummarySchema = dto({
  byLevel: counters,
  byDirection: counters,
  byStatus: counters,
  byOrderStatus: counters,
  total: z.number(),
})

export const SeriesSchema = dto({
  name: z.string(),
  unit: opt(z.string()),
  points: z.array(dto({ t: z.string(), v: z.number() })),
})

export const TimeSeriesResponseSchema = dto({
  series: z.array(SeriesSchema),
  markerAt: opt(z.string()),
})

/** GeoJSON не валидируем построчно: карта сама переживёт битую геометрию. */
export const FacilityCollectionSchema = dto({
  type: z.literal('FeatureCollection'),
  features: z.array(z.unknown()),
})

/** Трассы коллекторов. Тот же конверт, но приходит со своей ручки. */
export const LineCollectionSchema = dto({
  type: z.literal('FeatureCollection'),
  features: z.array(z.unknown()),
})

export const ModelMetricListSchema = z.array(ModelMetricSchema)
export const PredictionListSchema = z.array(PredictionSchema)

/** Конверт списка одинаков для всех списочных ручек. */
export function pageOf<T extends z.ZodType>(item: T) {
  return dto({
    items: z.array(item),
    page: z.number(),
    pageSize: z.number(),
    total: z.number(),
  })
}

export const PredictionPageSchema = pageOf(PredictionSchema)
export const WorkOrderPageSchema = pageOf(WorkOrderSchema)

/* ----------------------------------------------------------------- сессия */

export const CurrentUserSchema = dto({
  username: z.string(),
  fullName: z.string(),
  role: z.string(),
  roleLabel: z.string(),
  scopeKind: z.string(),
  scopeValue: opt(z.string()),
  permissions: z.array(z.string()),
})

export const LoginChallengeSchema = dto({
  status: z.string(),
  mfaToken: z.string(),
  secret: opt(z.string()),
  otpauthUrl: opt(z.string()),
})

export const SessionResponseSchema = dto({
  accessToken: z.string(),
  tokenType: z.string(),
  expiresIn: z.number(),
  user: CurrentUserSchema,
})

export const TestAccountSchema = dto({
  username: z.string(),
  fullName: z.string(),
  role: z.string(),
  roleLabel: z.string(),
  scopeKind: z.string(),
  scopeValue: opt(z.string()),
  mfaEnrolled: z.boolean(),
})

export const TestStandSchema = dto({
  enabled: z.boolean(),
  password: z.string(),
  sets: z.array(dto({ set: z.number(), accounts: z.array(TestAccountSchema) })),
})
