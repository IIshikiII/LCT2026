/**
 * Провайдер сессии. Отвечает на два вопроса: кто вошёл и как выйти.
 *
 * Стора здесь нет и не будет (ADR 0003): сессия — одно значение, и держать
 * ради него Zustand незачем. Хранилище — `sessionStorage`, а провайдер только
 * подписан на его изменения.
 *
 * Подписка нужна из-за ответа 401. HTTP-клиент снимает сессию сам, не зная о
 * React, и событие `arm-session-changed` доводит это до экрана.
 */
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { AuthContext } from './context'
import { SESSION_CHANGED, clearSession, readSession, writeSession } from './session'
import type { Session } from './session'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => readSession())

  useEffect(() => {
    const sync = () => setSession(readSession())
    globalThis.addEventListener(SESSION_CHANGED, sync)
    // Вход в соседней вкладке снимает экран входа и в этой.
    globalThis.addEventListener('storage', sync)
    return () => {
      globalThis.removeEventListener(SESSION_CHANGED, sync)
      globalThis.removeEventListener('storage', sync)
    }
  }, [])

  const signIn = useCallback((next: Session) => {
    writeSession(next)
    setSession(next)
  }, [])

  const signOut = useCallback(() => {
    clearSession()
    setSession(null)
  }, [])

  return <AuthContext value={{ session, signIn, signOut }}>{children}</AuthContext>
}
