/**
 * Все пути REST API в одном месте. Ни одной строки с URL за пределами этого файла.
 *
 * Добавляешь вызов — правишь три файла в одном коммите: этот, schemas.ts и
 * src/mocks/handlers.ts. См. docs/02-api-contract.md, раздел «Железное правило».
 */
export const endpoints = {
  /* Вход в систему. Первые два пути открыты, остальные требуют токен. */
  login: () => '/auth/login',
  /** Второй шаг входа. Он же подтверждает только что заведённый ключ. */
  mfa: () => '/auth/mfa',
  me: () => '/auth/me',
  logout: () => '/auth/logout',
  /** Наборы учёток тестового стенда. Открыты, наполняются только при флаге. */
  testAccounts: () => '/auth/test-accounts',
  testAccountSet: (set: number) => `/auth/test-accounts/${set}`,

  /** Описание предметной области. Грузится один раз при старте. */
  meta: () => '/meta',

  predictions: () => '/predictions',
  prediction: (id: string) => `/predictions/${id}`,
  predictionTimeseries: (id: string) => `/predictions/${id}/timeseries`,
  /** Единый эндпоинт действий над прогнозом (ADR 0004). */
  predictionAction: (id: string, code: string) => `/predictions/${id}/actions/${code}`,

  facilities: () => '/facilities',
  /**
   * Трассы коллекторов — отдельно от точек: геометрия сети не меняется, а
   * список объектов опрашивается раз в минуту. См. docs/02-api-contract.md.
   */
  facilityLines: () => '/facilities/lines',
  facility: (id: string) => `/facilities/${id}`,

  orders: () => '/orders',
  order: (id: string) => `/orders/${id}`,
  /** Единый эндпоинт действий над заявкой (ADR 0004). */
  orderAction: (id: string, code: string) => `/orders/${id}/actions/${code}`,

  modelMetrics: () => '/metrics/models',
  pipelineHealth: () => '/metrics/pipeline',
  dashboardSummary: () => '/dashboard/summary',
  dashboardTopRisks: () => '/dashboard/top-risks',
  /** Действующие уведомления о тревоге (ТЗ §10). */
  alerts: () => '/alerts',
} as const
