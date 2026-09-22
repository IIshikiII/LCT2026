/**
 * Ключи кэша TanStack Query.
 *
 * Ключ строится прямо из объекта фильтров, поэтому нормализация фильтров
 * (сортировка списков, выброс пустых значений) — не косметика, а условие того,
 * что кэш вообще работает. См. docs/06-url-state.md.
 */
import type { OrderFilters, PredictionFilters } from './filters'

export const queryKeys = {
  meta: () => ['meta'] as const,

  predictions: (f: PredictionFilters) => ['predictions', f] as const,
  prediction: (id: string) => ['prediction', id] as const,
  predictionTimeseries: (id: string) => ['prediction', id, 'timeseries'] as const,

  facilities: (f: PredictionFilters) => ['facilities', f] as const,
  facilityLines: () => ['facility-lines'] as const,

  orders: (f: OrderFilters) => ['orders', f] as const,
  order: (id: string) => ['order', id] as const,

  modelMetrics: () => ['metrics', 'models'] as const,
  pipelineHealth: () => ['metrics', 'pipeline'] as const,
  dashboardSummary: () => ['dashboard', 'summary'] as const,
  topRisks: (limit: number) => ['dashboard', 'top-risks', limit] as const,

  testStand: () => ['test-stand'] as const,
} as const
