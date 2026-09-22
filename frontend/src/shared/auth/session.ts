/**
 * Хранилище сессии. Единственное место, где живёт токен.
 *
 * Хранилище — `sessionStorage`, а не `localStorage`. Диспетчерская стоит в
 * общем помещении, и закрытая вкладка обязана закрывать смену. `localStorage`
 * пережил бы и вкладку, и перезапуск браузера.
 *
 * Модуль не знает ни о React, ни о HTTP-клиенте, поэтому его читает и клиент, и
 * контекст, и тесты. Обратной зависимости нет: клиент импортирует отсюда, а не
 * наоборот.
 */
import type { CurrentUser } from '@/shared/api/types'

export const SESSION_KEY = 'arm.session'

export interface Session {
  token: string
  user: CurrentUser
}

/** Событие «сессия сменилась». Его слушает контекст, чтобы перерисовать шапку. */
export const SESSION_CHANGED = 'arm-session-changed'

function storage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    // Браузер с запретом хранилища. Работать можно, сессия живёт до перезагрузки.
    return null
  }
}

export function readSession(): Session | null {
  const raw = storage()?.getItem(SESSION_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Session
    return parsed.token ? parsed : null
  } catch {
    // Испорченная запись — не повод падать. Она просто значит «входа нет».
    return null
  }
}

export function writeSession(session: Session): void {
  storage()?.setItem(SESSION_KEY, JSON.stringify(session))
  announce()
}

export function clearSession(): void {
  storage()?.removeItem(SESSION_KEY)
  announce()
}

/**
 * Токен для заголовка `Authorization`.
 *
 * Читается из хранилища на каждый запрос, а не кэшируется в переменной. Копия
 * в памяти разошлась бы с хранилищем при выходе из соседней вкладки, и клиент
 * продолжал бы слать снятый токен.
 */
export function currentToken(): string | null {
  return readSession()?.token ?? null
}

function announce(): void {
  globalThis.dispatchEvent?.(new Event(SESSION_CHANGED))
}
