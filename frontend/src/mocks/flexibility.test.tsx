/**
 * Главный критерий приёмки (spec §12): добавление нового направления не
 * требует правок кода — оно появляется в фильтрах, на карте и на дашборде само.
 *
 * Файл лежит в src/mocks/ намеренно: он единственный, кому нужно знать про
 * конкретное направление по имени, а домен по договорённости живёт только здесь
 * (ADR 0007).
 *
 * Новое направление добавлено одним файлом src/mocks/directions/floodRisk.ts и
 * одной строкой в src/mocks/directions/index.ts. Если этот тест позеленел, а
 * в src/ вне mocks/ не появилось ни строчки — архитектура работает.
 */
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { renderWithProviders } from '@/test/renderWithProviders'
import { db } from './db'
import userEvent from '@testing-library/user-event'
import { devFlags } from './devFlags'
import { floodRisk } from './directions'

const NEW = floodRisk.meta

describe('новое направление, о котором фронтенд не знает', () => {
  it('по умолчанию выключено — базовое состояние чистое', async () => {
    renderWithProviders(<AppShell />, { route: '/journal' })
    await screen.findByRole('table')
    expect(screen.queryByRole('checkbox', { name: NEW.label })).not.toBeInTheDocument()
  })

  it('появляется в фильтрах журнала', async () => {
    // Направления живут в выпадающем списке, а не плашками: плашками остался
    // только уровень риска. Смысл проверки прежний — новое направление
    // появляется в фильтрах само, без правок фронта.
    devFlags.override({ extraDirection: true })

    renderWithProviders(<AppShell />, { route: '/journal' })
    await screen.findByRole('table')

    await userEvent.click(screen.getByText('Подсистема'))
    expect(await screen.findByRole('checkbox', { name: NEW.label })).toBeInTheDocument()
  })

  it('фильтрует журнал по новому направлению', async () => {
    devFlags.override({ extraDirection: true })

    renderWithProviders(<AppShell />, {
      route: `/journal?direction=${NEW.code}`,
    })

    const table = await screen.findByRole('table')
    await waitFor(() => {
      expect(within(table).getAllByText(NEW.label).length).toBeGreaterThan(0)
    })
  })

  it('появляется на дашборде: и в разбивке, и в виджете метрик ТЗ', async () => {
    devFlags.override({ extraDirection: true })

    renderWithProviders(<AppShell />, { route: '/' })

    await waitFor(() => {
      // Разбивка по направлениям и таблица метрик — два независимых виджета,
      // ни один из них не знает списка направлений.
      expect(screen.getAllByText(NEW.label).length).toBeGreaterThanOrEqual(2)
    })
  })

  it('получает собственные прогнозы с блоками карточки', () => {
    devFlags.override({ extraDirection: true })

    const own = db().predictions.filter((p) => p.direction === NEW.code)
    expect(own.length).toBeGreaterThan(0)
    for (const prediction of own.slice(0, 5)) {
      expect(prediction.horizonHours).toBeGreaterThanOrEqual(NEW.minHorizonHours)
      expect(prediction.summary).toBeTruthy()
    }
  })

  it('получает строку в метриках моделей с теми же целевыми значениями', () => {
    devFlags.override({ extraDirection: true })

    const metric = db().metrics.find((m) => m.direction === NEW.code)
    expect(metric).toBeDefined()
    expect(metric?.targetPrecision).toBe(0.7)
    expect(metric?.targetRecall).toBe(0.5)
  })
})
