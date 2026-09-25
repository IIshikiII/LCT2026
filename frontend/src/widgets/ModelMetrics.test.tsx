/**
 * Критерий приёмки (spec §12): виджет показывает Precision и Recall против
 * целевых 0.7 и 0.5, а виджет конвейера — время расчёта против 5 минут и
 * минимальный горизонт против 24 часов.
 *
 * Это то, по чему жюри проверяет метрики ТЗ, не открывая ноутбук с моделью.
 */
import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { db } from '@/mocks/db'
import { renderWithProviders } from '@/test/renderWithProviders'
import { ModelMetrics } from './ModelMetrics'
import { PipelineHealth } from './PipelineHealth'
import { WidgetRenderer } from './widgetRegistry'

describe('виджет метрик моделей', () => {
  it('показывает строку на каждое направление с целевыми значениями', async () => {
    renderWithProviders(<ModelMetrics />)

    expect(await screen.findByText('Precision')).toBeInTheDocument()
    expect(screen.getByText('Recall')).toBeInTheDocument()

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(db().metrics.length)

    // Цели видны рядом с фактическими значениями, а не спрятаны в подсказку.
    expect(screen.getAllByText('цель 0,70').length).toBe(db().metrics.length)
    expect(screen.getAllByText('цель 0,50').length).toBe(db().metrics.length)
  })

  it('отмечает выполнение и невыполнение цели, а не только цветом', async () => {
    renderWithProviders(<ModelMetrics />)
    await screen.findAllByRole('listitem')

    const metrics = db().metrics.filter((m) => m.precision !== undefined)
    const good = metrics.find((m) => (m.precision ?? 0) >= m.targetPrecision)
    const bad = metrics.find((m) => (m.precision ?? 0) < m.targetPrecision)
    expect(good, 'в сиде должно быть направление с Precision выше цели').toBeDefined()
    expect(bad, 'и хотя бы одно ниже — иначе красное состояние не показать').toBeDefined()

    expect(screen.getAllByText(/✓/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/!/).length).toBeGreaterThan(0)
  })
})

describe('направление без замера точности', () => {
  it('пишет «не измерена» и пояснение сервера, а не ноль и не молчание', async () => {
    renderWithProviders(<ModelMetrics />)
    await screen.findAllByRole('listitem')

    const unmeasured = db().metrics.filter((m) => m.precision === undefined)
    expect(unmeasured.length, 'в сиде должно быть направление без замера').toBeGreaterThan(0)
    // Две ячейки на строку: Precision и Recall.
    expect(screen.getAllByText('не измерена')).toHaveLength(unmeasured.length * 2)
    for (const metric of unmeasured) {
      expect(screen.getByText(metric.note ?? '')).toBeInTheDocument()
    }
  })
})

describe('виджет конвейера', () => {
  it('сравнивает время расчёта и горизонт с целями ТЗ', async () => {
    renderWithProviders(<PipelineHealth />)

    expect(await screen.findByText('Время формирования прогноза')).toBeInTheDocument()
    expect(screen.getByText('Минимальный горизонт')).toBeInTheDocument()
    expect(screen.getByText(/цель < 5 мин/)).toBeInTheDocument()
    expect(screen.getByText(/цель ≥ 24 ч/)).toBeInTheDocument()
  })
})

describe('реестр виджетов', () => {
  it('рисует запасную панель для неизвестного кода, а не падает', () => {
    renderWithProviders(<WidgetRenderer code="виджет-из-будущего" />)

    const panel = screen.getByRole('region', { name: 'Виджет не реализован' })
    expect(within(panel).getByText('виджет-из-будущего')).toBeInTheDocument()
  })
})
