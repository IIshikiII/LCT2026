/**
 * Таблица журнала и заявок.
 *
 * Ничего не знает о предметной области: получает колонки из реестра и строки
 * из API. Виртуализации нет — пагинация серверная по 50 строк (spec §3).
 *
 * Доступность: строка — фокусируемый элемент, Enter и пробел открывают
 * карточку. Полный цикл работы диспетчера должен проходиться с клавиатуры
 * (docs/05-ui-kit.md).
 */
import type { KeyboardEvent, ReactNode } from 'react'
import type { AppMeta } from '@/shared/api/types'
import { cn } from '@/shared/lib/cn'
import { parseSort } from '@/shared/lib/urlState'
import { Icon } from '@/shared/ui/Icon'

export interface ColumnDef<Row> {
  key: string
  header: string
  /** Фиксированная ширина в пикселях. Без неё колонка тянется по содержимому. */
  width?: number
  align?: 'left' | 'right'
  sortable?: boolean
  cell: (row: Row, meta: AppMeta) => ReactNode
}

export interface DataTableProps<Row> {
  columns: ColumnDef<Row>[]
  rows: Row[]
  meta: AppMeta
  rowKey: (row: Row) => string
  selectedKey?: string
  onSelect?: (row: Row) => void
  /** Текущая сортировка в формате 'поле:asc'. */
  sort?: string
  onSort?: (field: string) => void
  /** Что показать вместо строк, когда их нет. */
  empty?: ReactNode
  caption: string
}

export function DataTable<Row>({
  columns,
  rows,
  meta,
  rowKey,
  selectedKey,
  onSelect,
  sort,
  onSort,
  empty,
  caption,
}: DataTableProps<Row>) {
  const sortState = parseSort(sort)

  if (rows.length === 0 && empty) {
    return <>{empty}</>
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, row: Row) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect?.(row)
    }
  }

  return (
    <div className="relative h-full overflow-auto">
      <table className="w-full border-collapse text-[13px]">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-panel">
          <tr>
            {columns.map((column) => {
              const active = sortState?.field === column.key
              return (
                <th
                  key={column.key}
                  scope="col"
                  style={column.width ? { width: column.width } : undefined}
                  aria-sort={active ? (sortState.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  className={cn(
                    'border-b border-line px-2 py-1.5 text-[12px] font-medium text-text-mute',
                    column.align === 'right' ? 'text-right' : 'text-left',
                  )}
                >
                  {column.sortable && onSort ? (
                    <button
                      type="button"
                      onClick={() => onSort(column.key)}
                      className={cn(
                        'inline-flex items-center gap-1 outline-offset-2',
                        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
                        active ? 'text-text' : 'hover:text-text-dim',
                      )}
                    >
                      {column.header}
                      {active ? (
                        <Icon
                          name="chevronDown"
                          size={12}
                          className={sortState.dir === 'asc' ? 'rotate-180' : undefined}
                        />
                      ) : null}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = rowKey(row)
            const selected = key === selectedKey
            return (
              <tr
                key={key}
                tabIndex={0}
                aria-selected={selected}
                onClick={() => onSelect?.(row)}
                onKeyDown={(event) => handleKeyDown(event, row)}
                className={cn(
                  'cursor-pointer border-b border-line/60 outline-offset-[-2px]',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
                  selected ? 'bg-raised' : 'hover:bg-raised',
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      'px-2 py-1.5 align-middle',
                      column.align === 'right' ? 'text-right' : 'text-left',
                    )}
                  >
                    {column.cell(row, meta)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
