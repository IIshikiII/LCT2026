/**
 * Сервер MSW для Vitest. Те же хендлеры, что и в браузере (ADR 0007): отдельных
 * фикстур в проекте нет, поэтому расхождению «в браузере работает, в тестах
 * нет» неоткуда взяться.
 */
import { setupServer } from 'msw/node'
import { handlers } from './handlers'

export const server = setupServer(...handlers)
