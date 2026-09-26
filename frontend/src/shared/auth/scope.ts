/**
 * Область видимости роли на экране.
 *
 * Что человек видит, решает сервер условием запроса к базе. Здесь только
 * подписи и решения вида «показывать ли фильтр»: строку, которую всё равно
 * не расширить, предлагать незачем.
 *
 * Виды области приходят строками, как и всё остальное из API. Списка ролей
 * интерфейс не держит (ADR 0002), и эти три значения — не роли, а способ
 * ограничения выборки.
 */
import type { AppMeta, CurrentUser } from '@/shared/api/types'

export const SCOPE_WHOLE = 'ALL'
export const SCOPE_DISTRICT = 'DISTRICT'
export const SCOPE_COMPLEX = 'COMPLEX'

/** Видит ли роль всё предприятие. */
export function seesWholeCompany(user: CurrentUser | undefined): boolean {
  return !user || user.scopeKind === SCOPE_WHOLE
}

/**
 * Район роли, если она ограничена одним районом.
 *
 * Карта прячет по нему чужие округа: показывать девять округов тому, кто
 * работает в одном, значит рисовать сеть, к которой его не допустили.
 */
export function scopeDistrict(user: CurrentUser | undefined): string | undefined {
  return user?.scopeKind === SCOPE_DISTRICT ? user.scopeValue : undefined
}

/**
 * Подпись области видимости для шапки.
 *
 * Район приходит кодом, и человеку нужен не код. Подпись берётся из `/meta`,
 * а неизвестный код показывается как есть: пустая строка хуже кода.
 */
export function scopeLabel(user: CurrentUser | undefined, meta: AppMeta): string {
  if (!user || user.scopeKind === SCOPE_WHOLE) return 'всё предприятие'

  const value = user.scopeValue ?? ''
  if (!value) return 'область не задана'

  if (user.scopeKind === SCOPE_DISTRICT) {
    return meta.districts.find((item) => item.code === value)?.label ?? value
  }
  return value
}
