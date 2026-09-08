/**
 * Произвольная таблица из карточки: допуски на работы, связанные объекты,
 * что угодно ещё — форму задаёт бэкенд через `{ columns, rows }`.
 */
import { z } from 'zod'
import type { CardBlock } from '@/shared/api/types'
import { fmtUnknown } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import { BlockFrame } from './BlockFrame'
import { GenericBlock } from './GenericBlock'

const Schema = z.looseObject({
  columns: z.array(
    z.looseObject({
      key: z.string(),
      header: z.string(),
      align: z.string().optional(),
    }),
  ),
  rows: z.array(z.looseObject({})),
})

/** Значения, похожие на дату, печатаем по-человечески — их в таблицах много. */
function cellText(value: unknown): string {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    return new Date(value).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }
  return fmtUnknown(value)
}

export function TableBlock({ block }: { block: CardBlock }) {
  const parsed = Schema.safeParse(block.data)
  if (!parsed.success) return <GenericBlock block={block} />

  const { columns, rows } = parsed.data

  if (rows.length === 0) {
    return (
      <BlockFrame title={block.title}>
        <p className="text-[13px] text-text-mute">Записей нет</p>
      </BlockFrame>
    )
  }

  return (
    <BlockFrame title={block.title}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={cn(
                    'border-b border-line px-1.5 py-1 font-medium text-text-mute',
                    column.align === 'right' ? 'text-right' : 'text-left',
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      'border-b border-line/50 px-1.5 py-1 text-text-dim',
                      column.align === 'right' ? 'text-right' : 'text-left',
                    )}
                  >
                    {cellText((row as Record<string, unknown>)[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </BlockFrame>
  )
}
