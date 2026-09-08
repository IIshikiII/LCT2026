/**
 * Единственное место, где читается import.meta.env.
 * Всё остальное приложение импортирует `env` отсюда — так значения видно в одном
 * месте, и их легко подменить в тестах.
 */
export const env = {
  /** Поднимать ли MSW. См. docs/04-mocks.md. */
  useMocks: import.meta.env.VITE_USE_MOCKS === 'true',
  /** База REST API, см. docs/02-api-contract.md. */
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  /** Ссылка на Swagger в шапке — четвёртый пункт итогового продукта по ТЗ. */
  apiDocsUrl: import.meta.env.VITE_API_DOCS_URL ?? '/api/v1/docs',
  /** Внешняя подложка карты. Пусто — офлайн-стиль, см. ADR 0008. */
  mapStyleUrl: import.meta.env.VITE_MAP_STYLE_URL ?? '',
} as const
