/**
 * Роль на экране: что видно и какие кнопки есть.
 *
 * Фронт ролью не ветвится. Границу видимости ставит сервер запросом к базе,
 * состав кнопок приходит полем `actions` (spec §5 правило 3). Поэтому тест
 * меняет только учётную запись и смотрит на результат.
 */
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { db } from '@/mocks/db'
import { MOCK_USERS, visibleTo } from '@/mocks/db/users'
import { renderWithProviders } from '@/test/renderWithProviders'
import { signInAs } from '@/test/session'

function userNamed(username: string) {
  return MOCK_USERS.find((item) => item.username === username)!
}

/** Сколько прогнозов видит роль по данным заглушки. */
function visibleCount(username: string): number {
  const user = userNamed(username)
  return db().predictions.filter((p) =>
    visibleTo(user, p.facility.district, p.facility.collector),
  ).length
}

async function rowCount(): Promise<number> {
  const table = await screen.findByRole('table')
  // Первая строка — заголовок таблицы.
  return within(table).getAllByRole('row').length - 1
}

describe('роль режет выборку', () => {
  it('диспетчер ОДС видит всё предприятие', async () => {
    signInAs('ods')
    renderWithProviders(<AppShell />, { route: '/journal' })

    await waitFor(async () => expect(await rowCount()).toBeGreaterThan(0))
    expect(visibleCount('ods')).toBe(db().predictions.length)
  })

  it('техник видит меньше строк, чем диспетчер ОДС', async () => {
    const narrow = visibleCount('tech')
    expect(narrow).toBeGreaterThan(0)
    expect(narrow).toBeLessThan(visibleCount('ods'))

    signInAs('tech')
    renderWithProviders(<AppShell />, { route: '/journal?pageSize=500' })

    await waitFor(async () => expect(await rowCount()).toBe(Math.min(narrow, 50)))
  })

  it('шапка называет роль и границу видимости', async () => {
    // Прежде слева стояло «АРМ диспетчера ОДС» при любой роли, и диспетчер
    // района читал чужую должность.
    signInAs('district')
    renderWithProviders(<AppShell />, { route: '/journal' })

    const header = await screen.findByRole('banner')
    expect(await within(header).findByText(/Диспетчер района/)).toBeInTheDocument()
    expect(within(header).queryByText(/АРМ диспетчера ОДС/)).toBeNull()
  })

  it('шапка диспетчера ОДС говорит, что видно всё предприятие', async () => {
    signInAs('ods')
    renderWithProviders(<AppShell />, { route: '/journal' })

    const header = await screen.findByRole('banner')
    expect(await within(header).findByText(/всё предприятие/)).toBeInTheDocument()
  })
})

describe('фильтр по району показывается только тому, кто видит всё', () => {
  it('диспетчер ОДС фильтр получает', async () => {
    signInAs('ods')
    renderWithProviders(<AppShell />, { route: '/journal' })

    expect(await screen.findByLabelText(/Район/)).toBeInTheDocument()
  })

  it('диспетчер района фильтра не получает', async () => {
    // Сузить нечего, а расширить нельзя: границу ставит сервер.
    signInAs('district')
    renderWithProviders(<AppShell />, { route: '/journal' })

    await screen.findByRole('table')
    expect(screen.queryByLabelText(/Район/)).toBeNull()
  })

  it('техник фильтра не получает', async () => {
    signInAs('tech')
    renderWithProviders(<AppShell />, { route: '/journal' })

    await screen.findByRole('table')
    expect(screen.queryByLabelText(/Район/)).toBeNull()
  })
})

describe('роль решает состав кнопок', () => {
  it('техник кнопок не получает', async () => {
    signInAs('tech')
    const user = userNamed('tech')
    const target = db().predictions.find(
      (p) => p.status === 'NEW' && visibleTo(user, p.facility.district, p.facility.collector),
    )!

    renderWithProviders(<AppShell />, { route: `/journal?prediction=${target.id}` })

    const panel = await screen.findByRole('complementary', { name: 'Карточка прогноза' })
    expect(await within(panel).findByText(/Действий по прогнозу/)).toBeInTheDocument()
  })

  it('диспетчер получает кнопку «Взять в работу»', async () => {
    signInAs('ods')
    const target = db().predictions.find((p) => p.status === 'NEW')!

    renderWithProviders(<AppShell />, { route: `/journal?prediction=${target.id}` })

    const panel = await screen.findByRole('complementary', { name: 'Карточка прогноза' })
    expect(
      await within(panel).findByRole('button', { name: 'Взять в работу' }),
    ).toBeInTheDocument()
  })
})
