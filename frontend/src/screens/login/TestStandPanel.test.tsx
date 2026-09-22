/**
 * Панель тестового стенда.
 *
 * Проверяется то, ради чего она сделана: проверяющий видит свободный набор,
 * берёт его или создаёт новый, а занятый набор отличает по отметке.
 *
 * В заглушках стенд включён всегда. Выключенный стенд проверяет бэкенд:
 * `backend/tests/test_test_stand.py`.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { DEMO_PASSWORD } from '@/mocks/db/users'
import { mockCodeFor } from '@/mocks/handlers'
import { renderWithProviders } from '@/test/renderWithProviders'
import { signOut } from '@/test/session'

async function panel(): Promise<HTMLElement> {
  return screen.findByRole('complementary', { name: 'Тестовые учётные записи' })
}

/** Блоки наборов. Заголовок пояснения тоже начинается со слова «Набор». */
function setsIn(host: HTMLElement): HTMLElement[] {
  return within(host)
    .getAllByText(/^Набор \d+$/)
    .map((node) => node.closest('section') as HTMLElement)
}

describe('панель тестового стенда', () => {
  beforeEach(() => {
    signOut()
  })

  it('показывает первый набор со всеми четырьмя ролями', async () => {
    renderWithProviders(<AppShell />, { route: '/journal' })
    const host = await panel()

    for (const login of ['ods', 'district', 'tech', 'crew']) {
      expect(await within(host).findByText(login)).toBeInTheDocument()
    }
    for (const role of ['Диспетчер ОДС', 'Диспетчер района', 'Техник', 'Группа реагирования']) {
      expect(within(host).getByText(role)).toBeInTheDocument()
    }
  })

  it('называет пароль', async () => {
    renderWithProviders(<AppShell />, { route: '/journal' })
    expect(await within(await panel()).findByText(DEMO_PASSWORD)).toBeInTheDocument()
  })

  it('отмечает, что второй фактор ещё не пройден', async () => {
    renderWithProviders(<AppShell />, { route: '/journal' })
    const host = await panel()

    expect(await within(host).findAllByText('2FA не пройдена')).toHaveLength(4)
  })

  it('отмечает пройденный второй фактор после входа', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })
    await panel()

    await user.type(screen.getByLabelText(/Логин/), 'ods')
    await user.type(screen.getByLabelText(/Пароль/), DEMO_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    await user.type(await screen.findByLabelText(/Код из приложения/), await mockCodeFor('ods'))
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))

    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Выйти' }))

    const host = await panel()
    await waitFor(() => expect(within(host).getAllByText('2FA пройдена')).toHaveLength(1))
  })

  it('создаёт ещё один набор', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })
    const host = await panel()
    expect(setsIn(host)).toHaveLength(1)

    await user.click(within(host).getByRole('button', { name: /Создать ещё один набор/ }))

    await waitFor(() => expect(setsIn(host)).toHaveLength(2))
    // Логины второго набора получают номер, иначе они столкнулись бы с первым.
    expect(within(host).getByText('ods-2')).toBeInTheDocument()
  })

  it('новым набором можно войти', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })
    const host = await panel()

    await user.click(within(host).getByRole('button', { name: /Создать ещё один набор/ }))
    await within(host).findByText('ods-2')

    await user.type(screen.getByLabelText(/Логин/), 'ods-2')
    await user.type(screen.getByLabelText(/Пароль/), DEMO_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    await user.type(await screen.findByLabelText(/Код из приложения/), await mockCodeFor('ods-2'))
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))

    expect(await screen.findByRole('table')).toBeInTheDocument()
  })

  it('удаляет набор целиком', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })
    const host = await panel()

    await user.click(within(host).getByRole('button', { name: /Создать ещё один набор/ }))
    await waitFor(() => expect(setsIn(host)).toHaveLength(2))

    await user.click(within(host).getByRole('button', { name: 'Удалить набор 2' }))

    await waitFor(() => expect(setsIn(host)).toHaveLength(1))
    expect(within(host).queryByText('ods-2')).toBeNull()
  })

  it('пропадает после входа', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })
    await panel()

    await user.type(screen.getByLabelText(/Логин/), 'ods')
    await user.type(screen.getByLabelText(/Пароль/), DEMO_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    await user.type(await screen.findByLabelText(/Код из приложения/), await mockCodeFor('ods'))
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))

    await screen.findByRole('table')
    expect(
      screen.queryByRole('complementary', { name: 'Тестовые учётные записи' }),
    ).toBeNull()
  })
})
