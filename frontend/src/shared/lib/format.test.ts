import { describe, expect, it } from 'vitest'
import {
  DASH,
  fmtDuration,
  fmtFacilityPath,
  fmtFreshness,
  fmtHours,
  fmtPct,
  fmtScore,
  fmtUnknown,
  isOverdue,
  plural,
} from './format'

describe('форматтеры', () => {
  it('возвращают прочерк на любом мусоре — следствие ADR 0006', () => {
    for (const bad of [undefined, null, '', 'не число', {}, NaN, []]) {
      expect(fmtPct(bad)).toBe(DASH)
      expect(fmtHours(bad)).toBe(DASH)
      expect(fmtScore(bad)).toBe(DASH)
      expect(fmtDuration(bad)).toBe(DASH)
    }
  })

  it('fmtPct переводит долю в проценты', () => {
    expect(fmtPct(0.734)).toBe('73 %')
    expect(fmtPct(1)).toBe('100 %')
  })

  it('fmtScore печатает метрики модели с запятой', () => {
    expect(fmtScore(0.712)).toBe('0,71')
  })

  it('fmtDuration читается человеком', () => {
    expect(fmtDuration(450)).toBe('450 мс')
    expect(fmtDuration(45_000)).toBe('45 с')
    expect(fmtDuration(138_000)).toBe('2 мин 18 с')
    expect(fmtDuration(120_000)).toBe('2 мин')
  })

  it('plural склоняет по-русски', () => {
    const forms: [string, string, string] = ['час', 'часа', 'часов']
    expect(plural(1, forms)).toBe('час')
    expect(plural(3, forms)).toBe('часа')
    expect(plural(11, forms)).toBe('часов')
    expect(plural(21, forms)).toBe('час')
    expect(plural(0, forms)).toBe('часов')
  })

  it('fmtFreshness укрупняет единицы', () => {
    expect(fmtFreshness(0)).toBe('только что')
    expect(fmtFreshness(2)).toBe('2 минуты назад')
    expect(fmtFreshness(90)).toBe('1 час назад')
  })

  it('fmtFacilityPath склеивает только заполненные уровни иерархии', () => {
    expect(fmtFacilityPath({ collector: 'К-3', chamber: 'Кам. 4' })).toBe('К-3 · Кам. 4')
    expect(fmtFacilityPath({})).toBe(DASH)
  })

  it('isOverdue сравнивает с переданным моментом, а не с системными часами', () => {
    const now = new Date('2026-09-08T12:00:00Z')
    expect(isOverdue('2026-09-08T11:00:00Z', now)).toBe(true)
    expect(isOverdue('2026-09-09T11:00:00Z', now)).toBe(false)
    expect(isOverdue(undefined, now)).toBe(false)
  })

  it('fmtUnknown безопасно печатает произвольные значения для GenericBlock', () => {
    expect(fmtUnknown(true)).toBe('да')
    expect(fmtUnknown(null)).toBe(DASH)
    expect(fmtUnknown('текст')).toBe('текст')
    expect(fmtUnknown({ a: 1 })).toBe('{"a":1}')
  })
})
