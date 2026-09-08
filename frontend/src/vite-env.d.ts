/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** true — приложение работает на заглушках из src/mocks (docs/04-mocks.md). */
  readonly VITE_USE_MOCKS?: string
  /** База REST API. Используется, когда VITE_USE_MOCKS != 'true'. */
  readonly VITE_API_BASE_URL?: string
  /** Ссылка на Swagger, показывается в шапке. */
  readonly VITE_API_DOCS_URL?: string
  /** Необязательная внешняя подложка карты. Пусто — офлайн-стиль (ADR 0008). */
  readonly VITE_MAP_STYLE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
