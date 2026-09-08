/**
 * Воркер MSW для дев-сервера. Поднимается только при VITE_USE_MOCKS=true —
 * см. единственную точку подключения в src/main.tsx.
 */
import { setupWorker } from 'msw/browser'
import { handlers } from './handlers'

export const worker = setupWorker(...handlers)

export async function startMockWorker(): Promise<void> {
  await worker.start({
    // Запросы, которых нет в хендлерах (шрифты, тайлы карты), идут в сеть как есть.
    onUnhandledRequest: 'bypass',
    quiet: true,
  })
  console.info('[mocks] заглушки подняты, приложение работает без бэкенда')
}
