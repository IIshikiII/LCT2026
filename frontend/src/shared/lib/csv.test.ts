import { describe, expect, it } from 'vitest'
import { csvFilename, toCsv } from './csv'

interface Row {
  id: string
  summary: string
  probability: number
}

const columns = [
  { key: 'id', header: 'Идентификатор', value: (r: Row) => r.id },
  { key: 'summary', header: 'Прогноз', value: (r: Row) => r.summary },
  { key: 'probability', header: 'Вероятность', value: (r: Row) => r.probability },
]

describe('toCsv', () => {
  it('склеивает заголовок и строки через точку с запятой', () => {
    const csv = toCsv([{ id: 'P-1', summary: 'текст', probability: 0.5 }], columns)
    expect(csv).toBe('Идентификатор;Прогноз;Вероятность\r\nP-1;текст;0.5')
  })

  it('экранирует значения с разделителем, кавычками и переводом строки', () => {
    const csv = toCsv(
      [{ id: 'P-2', summary: 'а;б "в"\nг', probability: 1 }],
      columns,
    )
    expect(csv.split('\r\n')[1]).toBe('P-2;"а;б ""в""\nг";1')
  })

  it('на пустой выборке отдаёт только заголовок', () => {
    expect(toCsv([], columns)).toBe('Идентификатор;Прогноз;Вероятность')
  })
})

describe('csvFilename', () => {
  it('подставляет дату', () => {
    expect(csvFilename('прогнозы', new Date('2026-09-08T10:00:00Z'))).toBe('прогнозы-2026-09-08.csv')
  })
})
