/**
 * Подписи области видимости.
 *
 * Район приходит кодом, и человеку нужен не код. Неизвестный код всё равно
 * показывается: пустая строка объясняет меньше, чем `CAO`.
 */
import { describe, expect, it } from 'vitest'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import type { AppMeta, CurrentUser } from '@/shared/api/types'
import { scopeDistrict, scopeLabel, seesWholeCompany } from './scope'

function user(scopeKind: string, scopeValue?: string): CurrentUser {
  return {
    username: 'кто-то',
    fullName: 'Кто-то К.',
    role: 'ROLE',
    roleLabel: 'Роль',
    scopeKind,
    scopeValue,
    permissions: [],
  }
}

const meta: AppMeta = {
  ...FALLBACK_META,
  districts: [{ code: 'CAO', label: 'Центральный' }],
}

describe('кто видит всё предприятие', () => {
  it('видит тот, у кого область ALL', () => {
    expect(seesWholeCompany(user('ALL'))).toBe(true)
  })

  it('не видит тот, кого ограничили районом или комплексом', () => {
    expect(seesWholeCompany(user('DISTRICT', 'CAO'))).toBe(false)
    expect(seesWholeCompany(user('COMPLEX', 'K-CAO-1'))).toBe(false)
  })

  it('без сессии считает, что ограничений нет', () => {
    // Экран входа рисуется до сессии, и прятать там нечего.
    expect(seesWholeCompany(undefined)).toBe(true)
  })
})

describe('район роли', () => {
  it('отдаётся только у роли, ограниченной районом', () => {
    expect(scopeDistrict(user('DISTRICT', 'CAO'))).toBe('CAO')
    expect(scopeDistrict(user('COMPLEX', 'K-CAO-1'))).toBeUndefined()
    expect(scopeDistrict(user('ALL'))).toBeUndefined()
  })
})

describe('подпись области', () => {
  it('всё предприятие названо словами, а не пустотой', () => {
    expect(scopeLabel(user('ALL'), meta)).toBe('всё предприятие')
  })

  it('район берёт подпись из меты', () => {
    expect(scopeLabel(user('DISTRICT', 'CAO'), meta)).toBe('Центральный')
  })

  it('неизвестный код показывается как есть', () => {
    // Мета могла не доехать, и код объясняет больше, чем пустая строка.
    expect(scopeLabel(user('DISTRICT', 'ZZZ'), meta)).toBe('ZZZ')
  })

  it('комплекс показывается своим значением', () => {
    expect(scopeLabel(user('COMPLEX', 'Коллектор «Тверской»'), meta)).toBe('Коллектор «Тверской»')
  })

  it('пустая область названа ошибкой, а не предприятием', () => {
    // Техник без комплекса заведён с ошибкой, и показывать ему всё нельзя.
    expect(scopeLabel(user('COMPLEX'), meta)).toBe('область не задана')
  })
})
