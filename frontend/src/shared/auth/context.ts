/**
 * Контекст сессии и хук доступа к ней.
 *
 * Лежит отдельно от провайдера намеренно: файл с компонентом обязан
 * экспортировать только компоненты, иначе перезагрузка модуля на лету теряет
 * состояние экрана.
 */
import { createContext, useContext } from 'react'
import type { Session } from './session'

export interface AuthValue {
  session: Session | null
  signIn: (session: Session) => void
  signOut: () => void
}

export const AuthContext = createContext<AuthValue | null>(null)

/**
 * Сессия и действия над ней.
 *
 * Вне провайдера падает намеренно: молчаливый `null` означал бы «никто не
 * вошёл», и экран показал бы форму входа вместо сообщения об ошибке сборки.
 */
export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (value === null) {
    throw new Error('useAuth вызван вне AuthProvider')
  }
  return value
}
