/**
 * Глобальная настройка Vitest.
 *
 * Сервер заглушек поднимается один раз на весь прогон: тесты ходят в те же
 * хендлеры, что и браузер (ADR 0007). Между тестами сбрасываются подменённые
 * хендлеры, флаги дев-панели и изменяемое состояние базы, иначе действия из
 * одного теста протекали бы в следующий.
 */
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest'
import { devFlags } from '@/mocks/devFlags'
import { resetMockDb } from '@/mocks/handlers'
import { signInAs } from './session'
import { server } from './msw'

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'bypass' })
})

beforeEach(() => {
  // Заглушки отказывают без токена так же, как сервер. Каждый тест экрана
  // начинается с открытой смены диспетчера ОДС, иначе он проверял бы вход, а
  // не то, ради чего написан. Тест роли переключается сам.
  signInAs('ods')
})

afterEach(() => {
  cleanup()
  server.resetHandlers()
  devFlags.reset()
  resetMockDb()
})

afterAll(() => {
  server.close()
})

/* --------------------------------------------------------- заглушки среды */

// Recharts измеряет контейнер через ResizeObserver, в jsdom его нет.
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}

// matchMedia требуется некоторым компонентам при первом рендере.
if (!globalThis.matchMedia) {
  globalThis.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof globalThis.matchMedia
}
