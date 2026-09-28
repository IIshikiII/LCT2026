/**
 * Виджет «Здоровье модели» сравнивает Precision и Recall модели с наивным
 * правилом на той же выборке. Умолчаний ТЗ 0.7 и 0.5 на нём нет: порогом
 * приёмки они не являются (ТЗ §9).
 *
 * Виджет конвейера показывает замер без подписей требований. Нарушенное
 * требование отмечено знаком «!».
 */
import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { db } from '@/mocks/db'
import { renderWithProviders } from '@/test/renderWithProviders'
import { ModelMetrics } from './ModelMetrics'
import { PipelineHealth } from './PipelineHealth'
import { WidgetRenderer } from './widgetRegistry'

describe('виджет «Здоровье модели»', () => {
  it('показывает строку на каждое направление и две группы: модель и правило', async () => {
    renderWithProviders(<ModelMetrics />)

    expect(screen.getByText('Здоровье модели')).toBeInTheDocument()
    expect(await screen.findByText('Модель')).toBeInTheDocument()
    expect(screen.getByText('Правило')).toBeInTheDocument()

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(db().metrics.length)
  })

  it('называет правило и показывает его числа рядом с числами модели', async () => {
    renderWithProviders(<ModelMetrics />)
    await screen.findAllByRole('listitem')

    const compared = db().metrics.filter((m) => m.baselineRule)
    expect(compared.length, 'в сиде должно быть направление с правилом').toBeGreaterThan(0)
    for (const metric of compared) {
      expect(screen.getByText(`правило: ${metric.baselineRule}`)).toBeInTheDocument()
    }
  })

  it('не показывает умолчания ТЗ и отметки выполнения', async () => {
    renderWithProviders(<ModelMetrics />)
    await screen.findAllByRole('listitem')

    expect(screen.queryByText(/цель/)).not.toBeInTheDocument()
    expect(screen.queryByText(/✓/)).not.toBeInTheDocument()
  })
})

describe('направление без замера точности', () => {
  it('пишет «не измерена» и пояснение сервера, а не ноль и не молчание', async () => {
    renderWithProviders(<ModelMetrics />)
    await screen.findAllByRole('listitem')

    const unmeasured = db().metrics.filter((m) => m.precision === undefined)
    expect(unmeasured.length, 'в сиде должно быть направление без замера').toBeGreaterThan(0)
    expect(screen.getAllByText('не измерена')).toHaveLength(unmeasured.length)
    for (const metric of unmeasured) {
      expect(screen.getByText(metric.note ?? '')).toBeInTheDocument()
    }
  })
})

describe('виджет конвейера', () => {
  it('показывает замер без подписей целей и без галочек', async () => {
    renderWithProviders(<PipelineHealth />)

    expect(await screen.findByText('Время формирования прогноза')).toBeInTheDocument()
    expect(screen.getByText('Горизонт прогноза')).toBeInTheDocument()
    expect(screen.getByText('Задержка потока')).toBeInTheDocument()
    expect(screen.queryByText(/цель/)).not.toBeInTheDocument()
    expect(screen.queryByText(/✓/)).not.toBeInTheDocument()
  })
})

describe('реестр виджетов', () => {
  it('рисует запасную панель для неизвестного кода, а не падает', () => {
    renderWithProviders(<WidgetRenderer code="виджет-из-будущего" />)

    const panel = screen.getByRole('region', { name: 'Виджет не реализован' })
    expect(within(panel).getByText('виджет-из-будущего')).toBeInTheDocument()
  })
})
