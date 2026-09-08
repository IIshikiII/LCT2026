/**
 * Критерий приёмки (spec §12): поломанная `/meta` не мешает приложению
 * подняться — интерфейс собирается на запасных конфигах.
 *
 * Это самый ценный тест устойчивости: `/meta` — единственная ручка, без которой
 * фронт в принципе не знает, что рисовать.
 */
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { devFlags } from '@/mocks/devFlags'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { renderWithProviders } from '@/test/renderWithProviders'

describe('приложение при недоступной /meta', () => {
  it('поднимается и рисует журнал на запасных конфигах', async () => {
    devFlags.override({ breakMeta: true })

    renderWithProviders(<AppShell />, { route: '/journal' })

    // Рельс на месте — каркас построен.
    expect(await screen.findByRole('navigation', { name: 'Разделы' })).toBeInTheDocument()

    // Колонки взяты из запасного состава.
    await waitFor(
      () => {
        expect(screen.getByRole('columnheader', { name: /Вероятность/ })).toBeInTheDocument()
      },
      { timeout: 5000 },
    )

    // Уровни риска из запасной меты доехали до фильтров.
    for (const level of FALLBACK_META.riskLevels) {
      expect(screen.getByRole('button', { name: level.label })).toBeInTheDocument()
    }
  })

  it('честно сообщает в шапке, что работает на запасной конфигурации', async () => {
    devFlags.override({ breakMeta: true })

    renderWithProviders(<AppShell />, { route: '/journal' })

    expect(await screen.findByText('запасная конфигурация', {}, { timeout: 5000 })).toBeInTheDocument()
  })

  it('при исправной /meta запасная конфигурация не упоминается', async () => {
    renderWithProviders(<AppShell />, { route: '/journal' })

    expect(await screen.findByRole('navigation', { name: 'Разделы' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: /Вероятность/ })).toBeInTheDocument()
    })
    expect(screen.queryByText('запасная конфигурация')).not.toBeInTheDocument()
  })
})
