import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { WorkOrder } from '@/shared/api/types'
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

describe('колонка срока', () => {
  /** Заявка с давно прошедшим сроком — просрочка гарантирована. */
  const order = (status: string): WorkOrder => ({
    id: 'WO-0001',
    number: '2026-0001',
    predictionId: 'P-0001',
    facility: {
      id: 'F-1',
      collector: 'Тверской',
      district: 'CAO',
      address: 'ул. Пятницкая, д. 1',
      lat: 55.7,
      lon: 37.6,
    },
    workType: 'Замена датчика',
    dueAt: '2020-01-01T00:00:00Z',
    status,
    createdAt: '2019-12-01T00:00:00Z',
    actions: [],
  })

  const renderDue = (status: string) =>
    render(<>{orderColumns['dueAt']!.cell(order(status), FALLBACK_META)}</>)

  it('показывает просрочку, пока заявка в работе', () => {
    renderDue('IN_PROGRESS')
    expect(screen.getByText(/просрочено/)).toBeInTheDocument()
  })

  it('молчит о просрочке у выполненной заявки', () => {
    renderDue('DONE')
    expect(screen.queryByText(/просрочено/)).not.toBeInTheDocument()
  })

  it('молчит о просрочке у отклонённой заявки', () => {
    renderDue('REJECTED')
    expect(screen.queryByText(/просрочено/)).not.toBeInTheDocument()
  })
})
