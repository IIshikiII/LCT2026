/**
 * Точка входа.
 *
 * Единственное место во всём приложении, которое знает о существовании
 * заглушек (ADR 0007). Воркер MSW поднимается ДО монтирования React, иначе
 * первые запросы уйдут в сеть мимо перехватчика.
 *
 * Дев-панель монтируется отдельным корнем: так код приложения не импортирует
 * ничего из src/mocks/, и удаление этой папки не ломает сборку.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { AppShell } from './app/AppShell'
import { AppProviders, queryClient } from './app/providers'
import { env } from './shared/config/env'

/**
 * Адрес воркера MapLibre. Нужен только собранной версии.
 *
 * Разбор геометрии карты идёт в веб-воркере, и MapLibre ищет его соседним
 * файлом. В сборке соседа рядом не оказывается: Rollup складывает
 * `maplibre-gl` в общий чанк, а `.mjs` воркера в `dist/` не попадает. Запрос
 * уходит в никуда, и карта становится чёрным прямоугольником — без ошибки в
 * консоли и без события `map.on('error')`. Единственный след — запрос за
 * воркером, навсегда застрявший в состоянии pending.
 *
 * Поэтому в сборке адрес задаётся явно: `?worker&url` собирает воркер вместе
 * с зависимостями в отдельный ассет и отдаёт его адрес.
 *
 * **В деве этого делать нельзя, и импорт тоже должен отсутствовать.** По
 * такому адресу Vite отдаёт не файл, а свою обёртку `?worker_file&type=module`,
 * и загрузчик MapLibre виснет на ней навсегда — даже если `setWorkerUrl` не
 * вызывать, один лишь импорт ломает карту. Ветка отсекается на этапе сборки,
 * поэтому в деве этого модуля не существует вовсе, и MapLibre находит соседа
 * сам. Чтобы сосед был на месте, пакет исключён из предбандлинга:
 * `optimizeDeps.exclude` в vite.config.ts.
 */
async function wireMapWorker(): Promise<void> {
  if (!import.meta.env.PROD) return
  const [{ setWorkerUrl }, { default: workerUrl }] = await Promise.all([
    import('maplibre-gl'),
    import('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'),
  ])
  setWorkerUrl(workerUrl)
}

async function bootstrap(): Promise<void> {
  await wireMapWorker()

  if (env.useMocks) {
    const { startMockWorker } = await import('./mocks/browser')
    await startMockWorker()
  }

  const container = document.getElementById('root')
  if (!container) throw new Error('Не найден узел #root')

  createRoot(container).render(
    <StrictMode>
      <AppProviders>
        <AppShell />
      </AppProviders>
    </StrictMode>,
  )

  if (env.useMocks) {
    // Смена тумблера дев-панели меняет данные «бэкенда» — сбрасываем кэш.
    window.addEventListener('mock-flags-changed', () => {
      void queryClient.invalidateQueries()
    })

    const { DevPanel } = await import('./mocks/DevPanel')
    const devRoot = document.createElement('div')
    document.body.appendChild(devRoot)
    createRoot(devRoot).render(<DevPanel />)
  }
}

void bootstrap()
