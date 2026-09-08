import { describe, expect, it } from 'vitest'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { orderColumns, predictionColumns, resolveColumns } from './columnRegistry'

describe('resolveColumns', () => {
  it('сохраняет порядок ключей из меты', () => {
    const keys = ['status', 'risk', 'summary']
    expect(resolveColumns(keys, predictionColumns).map((c) => c.key)).toEqual(keys)
  })

  it('молча отбрасывает колонку, которой нет в реестре', () => {
    const columns = resolveColumns(['risk', 'колонка-из-будущего', 'status'], predictionColumns)
    expect(columns.map((c) => c.key)).toEqual(['risk', 'status'])
  })

  it('на пустом списке ключей не падает', () => {
    expect(resolveColumns([], predictionColumns)).toEqual([])
  })

  it('покрывает весь запасной состав колонок журнала', () => {
    const resolved = resolveColumns(FALLBACK_META.journalColumns, predictionColumns)
    expect(resolved).toHaveLength(FALLBACK_META.journalColumns.length)
  })

  it('покрывает весь запасной состав колонок заявок', () => {
    const resolved = resolveColumns(FALLBACK_META.orderColumns, orderColumns)
    expect(resolved).toHaveLength(FALLBACK_META.orderColumns.length)
  })
})
