/**
 * Выгрузка текущей выборки журнала в CSV (spec §9).
 *
 * Два неочевидных решения, оба ради Excel, в котором это и будут открывать:
 *  - разделитель «;», потому что в русской локали Excel запятая — десятичный знак;
 *  - BOM в начале файла, иначе кириллица открывается кракозябрами.
 */
export interface CsvColumn<Row> {
  key: string
  header: string
  value: (row: Row) => unknown
}

const SEP = ';'

function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /["\n\r;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function toCsv<Row>(rows: Row[], columns: CsvColumn<Row>[]): string {
  const head = columns.map((c) => escapeCell(c.header)).join(SEP)
  const body = rows.map((row) => columns.map((c) => escapeCell(c.value(row))).join(SEP))
  return [head, ...body].join('\r\n')
}

/** Отдаёт файл браузеру. В jsdom не вызывается — логика склейки тестируется отдельно. */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** Имя файла с датой: «прогнозы-2026-09-08.csv». */
export function csvFilename(prefix: string, now: Date = new Date()): string {
  const iso = now.toISOString().slice(0, 10)
  return `${prefix}-${iso}.csv`
}
