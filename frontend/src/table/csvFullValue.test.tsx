/**
 * Обрезка в таблице не должна доходить до выгрузки.
 *
 * В журнале длинный адрес объекта и длинная фраза прогноза обрезаются
 * многоточием: иначе колонка «Прогноз» сжимается до нечитаемой. В CSV эти же
 * поля обязаны уйти целиком — файл открывают в Excel, чтобы разбираться, а не
 * любоваться.
 *
 * Проверяется именно связка: ячейка коротка, колонка выгрузки полна.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Prediction } from '@/shared/api/types'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { toCsv } from '@/shared/lib/csv'
import { csvColumnsFor, predictionColumns } from './columnRegistry'

const LONG_ADDRESS = 'Коллектор северо-восточный магистральный, пикет 148, камера обслуживания'
const LONG_SUMMARY =
  'Несанкционированный доступ: восемь ночных срабатываний за тридцать суток без ' +
  'действующего допуска на работы, рядом идут работы подрядчика'

const row: Prediction = {
  id: 'P-1',
  direction: 'UNAUTHORIZED_ACCESS',
  level: 'HIGH',
  probability: 0.63,
  horizonHours: 24,
  computedAt: '2026-09-23T17:40:00Z',
  computeMs: 12,
  status: 'NEW',
  summary: LONG_SUMMARY,
  facility: {
    id: 'F-0001',
    address: LONG_ADDRESS,
    collector: 'K-SVAO-1',
    district: 'SVAO',
    section: 'У-3',
    chamber: 'К-3',
    lat: 55.8,
    lon: 37.6,
  },
}

function cellText(key: string): string {
  const column = predictionColumns[key]
  if (!column) throw new Error(`нет колонки ${key}`)
  const { container } = render(
    <MemoryRouter>{column.cell(row, FALLBACK_META)}</MemoryRouter>,
  )
  return container.textContent ?? ''
}

function csvValue(key: string): string {
  const columns = csvColumnsFor([key], FALLBACK_META)
  // Первая строка — заголовок, вторая — данные.
  return toCsv([row], columns).split('\r\n')[1] ?? ''
}

describe('длинные значения в таблице и в выгрузке', () => {
  it('ячейка объекта показывает адрес и путь', () => {
    const text = cellText('facility')

    expect(text).toContain(LONG_ADDRESS)
    expect(text).toContain('K-SVAO-1')
  })

  it('полный адрес остаётся в подсказке', () => {
    // Обрезает CSS, а не JavaScript: в разметке текст целиком, и подсказка
    // даёт его прочитать, не открывая карточку.
    render(<MemoryRouter>{predictionColumns['facility']?.cell(row, FALLBACK_META)}</MemoryRouter>)
    const node = screen.getByTitle(new RegExp(LONG_ADDRESS))

    expect(node).toHaveClass('min-w-0')
  })

  it('выгрузка отдаёт адрес и путь целиком', () => {
    const value = csvValue('facility')

    expect(value).toContain(LONG_ADDRESS)
    expect(value).toContain('K-SVAO-1 · У-3 · К-3')
  })

  it('фраза прогноза в ячейке ограничена двумя строками', () => {
    render(<MemoryRouter>{predictionColumns['summary']?.cell(row, FALLBACK_META)}</MemoryRouter>)

    expect(screen.getByTitle(LONG_SUMMARY)).toHaveClass('line-clamp-2')
  })

  it('выгрузка отдаёт фразу прогноза целиком', () => {
    expect(csvValue('summary')).toContain(LONG_SUMMARY)
  })
})
