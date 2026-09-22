/**
 * Ворота перед периметром: вход, второй фактор, регистрация ключа, выход.
 *
 * Проверяется то, что видит человек, а не то, как устроен токен. Главное
 * свойство: пароль один в систему не пускает.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { DEMO_PASSWORD } from '@/mocks/db/users'
import { renderWithProviders } from '@/test/renderWithProviders'
import { signOut } from '@/test/session'

/** Любые шесть цифр: заглушка не считает код по алгоритму TOTP. */
const CODE = '123456'

async function fillPassword(user: ReturnType<typeof userEvent.setup>, username: string) {
  await user.type(screen.getByLabelText(/Логин/), username)
  await user.type(screen.getByLabelText(/Пароль/), DEMO_PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Войти' }))
}

describe('вход в систему', () => {
  beforeEach(() => {
    // Общая настройка тестов открывает смену. Здесь проверяется сам вход.
    signOut()
  })

  it('показывает форму входа вместо экранов, пока смены нет', () => {
    renderWithProviders(<AppShell />, { route: '/journal' })

    expect(screen.getByLabelText(/Логин/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('пароль без кода не пускает внутрь', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'ods')

    expect(await screen.findByLabelText(/Код из приложения/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('код открывает смену и показывает имя и роль в шапке', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'ods')
    await user.type(await screen.findByLabelText(/Код из приложения/), CODE)
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))

    const header = await screen.findByRole('banner')
    expect(await within(header).findByText('Иванов И. И.')).toBeInTheDocument()
    expect(within(header).getByText(/Диспетчер ОДС/)).toBeInTheDocument()
    expect(await screen.findByRole('table')).toBeInTheDocument()
  })

  it('не тот пароль оставляет на первом шаге и объясняет причину', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await user.type(screen.getByLabelText(/Логин/), 'ods')
    await user.type(screen.getByLabelText(/Пароль/), 'не тот')
    await user.click(screen.getByRole('button', { name: 'Войти' }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Код из приложения/)).toBeNull()
  })

  it('не тот код не открывает смену', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'ods')
    await user.type(await screen.findByLabelText(/Код из приложения/), 'абвгде')
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('записи без ключа показывает секрет и ссылку для аутентификатора', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'crew')

    expect(await screen.findByText(/Ключ для ручного ввода/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Открыть в приложении/ })
    expect(link).toHaveAttribute('href', expect.stringContaining('otpauth://totp/'))
  })

  it('выход возвращает к форме входа', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'ods')
    await user.type(await screen.findByLabelText(/Код из приложения/), CODE)
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: 'Выйти' }))

    await waitFor(() => expect(screen.getByLabelText(/Логин/)).toBeInTheDocument())
  })
})
