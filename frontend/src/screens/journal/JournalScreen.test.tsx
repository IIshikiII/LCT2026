/**
 * Журнал целиком, поверх заглушек.
 *
 * Критерий приёмки (spec §12): ссылка на отфильтрованный журнал с открытой
 * карточкой восстанавливает то же состояние. Плюс работа с клавиатуры — без неё
 * полный цикл диспетчера на защите не пройти.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { db } from '@/mocks/db'
import { currentSearch, renderWithProviders } from '@/test/renderWithProviders'

describe('журнал прогнозов', () => {
  it('рисует колонки из меты и строки из API', async () => {
    renderWithProviders(<AppShell />, { route: '/journal' })

    const table = await screen.findByRole('table')
    for (const header of ['Рассчитан', 'Направление', 'Объект', 'Вероятность', 'Горизонт']) {
      expect(within(table).getByRole('columnheader', { name: new RegExp(header) })).toBeInTheDocument()
    }
    expect(within(table).getAllByRole('row').length).toBeGreaterThan(5)
  })

  it('восстанавливает фильтр из ссылки и показывает только отфильтрованное', async () => {
    renderWithProviders(<AppShell />, { route: '/journal?level=CRITICAL' })

    await screen.findByRole('table')
    expect(screen.getByRole('button', { name: 'Критический' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    const expected = db().predictions.filter((p) => p.level === 'CRITICAL').length
    await waitFor(() => {
      // Строк ровно столько, сколько критических прогнозов в сиде, плюс заголовок.
      expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(expected + 1)
    })
  })

  it('восстанавливает открытую карточку из ссылки', async () => {
    const target = db().predictions[3]!

    renderWithProviders(<AppShell />, { route: `/journal?prediction=${target.id}` })

    const panel = await screen.findByRole('complementary', { name: 'Карточка прогноза' })
    expect(await within(panel).findByText(target.facility.address)).toBeInTheDocument()
  })

  it('клик по строке кладёт выбранный прогноз в URL и открывает панель', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    const table = await screen.findByRole('table')
    const firstRow = within(table).getAllByRole('row')[1]!
    await user.click(firstRow)

    await waitFor(() => expect(currentSearch()).toContain('prediction='))
    expect(await screen.findByRole('complementary', { name: 'Карточка прогноза' })).toBeInTheDocument()
  })

  it('открывает карточку с клавиатуры — Enter на строке', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    const table = await screen.findByRole('table')
    const firstRow = within(table).getAllByRole('row')[1]!
    firstRow.focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(currentSearch()).toContain('prediction='))
  })

  it('закрывает панель по Escape, не теряя фильтр', async () => {
    const user = userEvent.setup()
    const target = db().predictions[5]!

    renderWithProviders(<AppShell />, {
      route: `/journal?level=HIGH&prediction=${target.id}`,
    })

    await screen.findByRole('complementary', { name: 'Карточка прогноза' })
    await user.keyboard('{Escape}')

    await waitFor(() => {
      expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    })
    expect(currentSearch()).toContain('level=HIGH')
  })

  it('сортирует по клику на заголовок и пишет сортировку в URL', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    const table = await screen.findByRole('table')
    await user.click(within(table).getByRole('button', { name: /Вероятность/ }))

    await waitFor(() => expect(currentSearch()).toContain('sort=probability%3Adesc'))
  })

  it('показывает пустое состояние с подсказкой, когда фильтры ничего не находят', async () => {
    renderWithProviders(<AppShell />, { route: '/journal?status=НЕСУЩЕСТВУЮЩИЙ' })

    expect(await screen.findByText('Прогнозов по этим условиям нет')).toBeInTheDocument()
    expect(screen.getByText(/Снимите часть фильтров/)).toBeInTheDocument()
  })

  it('сбрасывает страницу при смене фильтра', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal?page=3' })

    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Высокий' }))

    await waitFor(() => expect(currentSearch()).not.toContain('page='))
  })
})
