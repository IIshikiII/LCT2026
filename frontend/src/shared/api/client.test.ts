/**
 * Критерии приёмки, которые проверяет этот файл (spec §12):
 *  - лишнее поле в ответе API — не ошибка;
 *  - несовпадение схемы не бросает исключение, а деградирует.
 *
 * Плюс инварианты buildQuery, от которых зависит стабильность ключей кэша.
 */
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { server } from '@/test/msw'
import { ApiError, apiGet, buildQuery, parseTolerant } from './client'

const Schema = z.looseObject({ id: z.string(), n: z.number() })

describe('buildQuery', () => {
  it('выбрасывает пустые значения вместо ?key=', () => {
    expect(buildQuery({ a: '', b: undefined, c: null, d: 'x' })).toBe('?d=x')
  })

  it('сортирует повторяемые значения, чтобы ключ кэша был стабильным', () => {
    expect(buildQuery({ level: ['HIGH', 'LOW'] })).toBe(buildQuery({ level: ['LOW', 'HIGH'] }))
  })

  it('сортирует имена параметров', () => {
    expect(buildQuery({ b: 1, a: 2 })).toBe('?a=2&b=1')
  })

  it('на пустом объекте возвращает пустую строку', () => {
    expect(buildQuery({})).toBe('')
    expect(buildQuery()).toBe('')
  })
})

describe('parseTolerant', () => {
  it('возвращает разобранные данные вместе с неизвестными полями', () => {
    const out = parseTolerant<Record<string, unknown>>(
      Schema,
      { id: 'a', n: 1, СовершенноНовоеПоле: true },
      '/test',
    )
    expect(out.id).toBe('a')
    expect(out['СовершенноНовоеПоле']).toBe(true)
  })

  it('не бросает на несовпадении схемы и отдаёт данные как есть', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const raw = { id: 42, n: 'не число' }
    const out = parseTolerant<Record<string, unknown>>(Schema, raw, '/test')
    expect(out).toEqual(raw)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })
})

describe('apiGet', () => {
  it('переживает ответ с переименованным обязательным полем', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    server.use(
      http.get('*/api/v1/__test/renamed', () => HttpResponse.json({ identifier: 'a', n: 1 })),
    )
    const out = await apiGet<Record<string, unknown>>('/__test/renamed', Schema)
    expect(out['identifier']).toBe('a')
    warn.mockRestore()
  })

  it('бросает ApiError на коде 500 — его поймает TanStack Query', async () => {
    server.use(
      http.get('*/api/v1/__test/boom', () => new HttpResponse(null, { status: 500 })),
    )
    await expect(apiGet('/__test/boom', Schema)).rejects.toBeInstanceOf(ApiError)
  })

  it('бросает ApiError, если тело не JSON', async () => {
    server.use(http.get('*/api/v1/__test/html', () => HttpResponse.text('<html/>')))
    await expect(apiGet('/__test/html', Schema)).rejects.toBeInstanceOf(ApiError)
  })
})
