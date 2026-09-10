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
  section: z.string().optional(),
  chamber: z.string().optional(),
  device: z.string().optional(),
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
  required: z.boolean().optional(),
  minLength: z.number().optional(),
  optionsRef: z.string().optional(),
  placeholder: z.string().optional(),
  help: z.string().optional(),
})

export const ActionDefSchema = dto({
  code: z.string(),
  label: z.string(),
  kind: z.string(),
  confirm: z.string().optional(),
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
  orderId: z.string().optional(),
})

export const PredictionDetailSchema = dto({
  ...PredictionSchema.shape,
  blocks: z.array(CardBlockSchema),
  actions: z.array(ActionDefSchema),
})

export const WorkOrderOutcomeSchema = dto({
  actualCause: z.string(),
  predictionConfirmed: z.boolean(),
  comment: z.string(),
  closedAt: z.string(),
})

export const WorkOrderSchema = dto({
  id: z.string(),
  number: z.string(),
  predictionId: z.string(),
  facility: FacilityRefSchema,
  workType: z.string(),
  dueAt: z.string(),
  status: z.string(),
  createdAt: z.string(),
  actions: z.array(ActionDefSchema),
  outcome: WorkOrderOutcomeSchema.optional(),
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
  unit: z.string().optional(),
  points: z.array(dto({ t: z.string(), v: z.number() })),
})

export const TimeSeriesResponseSchema = dto({
  series: z.array(SeriesSchema),
  markerAt: z.string().optional(),
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
