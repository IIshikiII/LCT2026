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

async function bootstrap(): Promise<void> {
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
