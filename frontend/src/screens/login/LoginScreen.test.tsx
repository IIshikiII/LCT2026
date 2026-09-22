/**
 * Ворота перед периметром: вход, второй фактор, регистрация ключа, выход.
 *
 * Проверяется то, что видит человек, а не то, как устроен токен. Главное
 * свойство: пароль один в систему не пускает.
 *
 * Код считается, а не выдумывается: заглушка проверяет TOTP по-настоящему, и
 * строка `123456` ей не подходит.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { mockCodeFor } from '@/mocks/handlers'
import { DEMO_PASSWORD } from '@/mocks/db/users'
import { renderWithProviders } from '@/test/renderWithProviders'
import { signOut } from '@/test/session'

async function fillPassword(user: ReturnType<typeof userEvent.setup>, username: string) {
  await user.type(screen.getByLabelText(/Логин/), username)
  await user.type(screen.getByLabelText(/Пароль/), DEMO_PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Войти' }))
}

/**
 * Вводит настоящий код. Заглушка считает TOTP по-честному, поэтому выдумать
 * шесть цифр нельзя — их надо посчитать по тому же секрету, что ушёл в QR.
 */
async function fillCode(user: ReturnType<typeof userEvent.setup>, username: string) {
  const field = await screen.findByLabelText(/Код из приложения/)
  await user.type(field, await mockCodeFor(username))
  await user.click(screen.getByRole('button', { name: 'Подтвердить' }))
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
    await fillCode(user, 'ods')

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

    // Человеку нужен текст сервера, а не код ответа: он объясняет, что делать.
    expect(await screen.findByRole('alert')).toHaveTextContent(/Логин или пароль не подошли/)
    expect(screen.queryByLabelText(/Код из приложения/)).toBeNull()
  })

  it('не тот код не открывает смену', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'ods')
    await user.type(await screen.findByLabelText(/Код из приложения/), 'абвгде')
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Код не подошёл/)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('записи без ключа показывает QR для сканирования', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppShell />, { route: '/journal' })

    await fillPassword(user, 'crew')

    expect(
      await screen.findByRole('img', { name: /QR-код для приложения/ }),
    ).toBeInTheDocument()
  })

  it('оставляет запасной путь: ключ руками и ссылку', async () => {
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
    await fillCode(user, 'ods')
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: 'Выйти' }))

    await waitFor(() => expect(screen.getByLabelText(/Логин/)).toBeInTheDocument())
  })
})
