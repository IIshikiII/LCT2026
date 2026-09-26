/**
 * Открытая смена для тестов.
 *
 * Заглушки отказывают без токена так же, как сервер, поэтому каждый тест
 * экрана начинается с записанной сессии. Роль меняется одной строкой:
 * `signInAs('tech')` — и тот же экран показывает границу видимости техника.
 */
import { MOCK_USERS } from '@/mocks/db/users'
import { SESSION_KEY } from '@/shared/auth/session'

export function signInAs(username: string): void {
  const user = MOCK_USERS.find((item) => item.username === username)
  if (!user) throw new Error(`в заглушках нет учётной записи ${username}`)

  sessionStorage.setItem(
    SESSION_KEY,
    JSON.stringify({
      token: `mock.access.${user.username}`,
      user: {
        username: user.username,
        fullName: user.fullName,
        role: user.role,
        roleLabel: user.roleLabel,
        scopeKind: user.scopeKind,
        scopeValue: user.scopeValue,
        permissions: user.permissions,
      },
    }),
  )
}

export function signOut(): void {
  sessionStorage.removeItem(SESSION_KEY)
}
