/**
 * Все пути REST API в одном месте. Ни одной строки с URL за пределами этого файла.
 *
 * Добавляешь вызов — правишь три файла в одном коммите: этот, schemas.ts и
 * src/mocks/handlers.ts. См. docs/02-api-contract.md, раздел «Железное правило».
 */
export const endpoints = {
  /** Описание предметной области. Грузится один раз при старте. */
  meta: () => '/meta',

  predictions: () => '/predictions',
  prediction: (id: string) => `/predictions/${id}`,
  predictionTimeseries: (id: string) => `/predictions/${id}/timeseries`,
  /** Единый эндпоинт действий над прогнозом (ADR 0004). */
  predictionAction: (id: string, code: string) => `/predictions/${id}/actions/${code}`,

  facilities: () => '/facilities',
  facility: (id: string) => `/facilities/${id}`,

  orders: () => '/orders',
  order: (id: string) => `/orders/${id}`,
  /** Единый эндпоинт действий над заявкой (ADR 0004). */
  orderAction: (id: string, code: string) => `/orders/${id}/actions/${code}`,

  modelMetrics: () => '/metrics/models',
  pipelineHealth: () => '/metrics/pipeline',
  dashboardSummary: () => '/dashboard/summary',
  dashboardTopRisks: () => '/dashboard/top-risks',
} as const
