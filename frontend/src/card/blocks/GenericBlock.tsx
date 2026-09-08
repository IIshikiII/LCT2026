/**
 * Запасной рендерер блока — самый важный компонент карточки.
 *
 * Рисует любой JSON: объект — списком пар «ключ — значение», массив объектов —
 * таблицей, массив примитивов — перечислением. Благодаря ему блок неизвестного
 * типа отображается сразу, ещё до того, как для него написан компонент
 * (ADR 0003, критерий приёмки spec §12).
 *
 * Правило при доработках: этот компонент не имеет права бросать исключение ни
 * на каких данных. Глубина рекурсии ограничена, циклические ссылки не
 * встречаются — данные приходят из JSON.
 */
import type { CardBlock } from '@/shared/api/types'
import { fmtUnknown } from '@/shared/lib/format'
import { BlockFrame } from './BlockFrame'

const MAX_DEPTH = 4

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isObjectArray(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.length > 0 && value.every(isPlainObject)
}

function ValueView({ value, depth }: { value: unknown; depth: number }) {
  if (depth >= MAX_DEPTH) return <span className="text-text-mute">…</span>

  if (isObjectArray(value)) {
    const columns = [...new Set(value.flatMap((row) => Object.keys(row)))]
    return (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr>
              {columns.map((key) => (
                <th key={key} className="border-b border-line px-1.5 py-1 text-left text-text-mute">
                  {key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {value.slice(0, 25).map((row, index) => (
              <tr key={index}>
                {columns.map((key) => (
                  <td key={key} className="border-b border-line/50 px-1.5 py-1 align-top text-text-dim">
                    {fmtUnknown(row[key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  if (Array.isArray(value)) {
    return (
      <ul className="flex flex-col gap-0.5">
        {value.slice(0, 25).map((item, index) => (
          <li key={index} className="text-text-dim">
            <ValueView value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    )
  }

  if (isPlainObject(value)) {
    return <PairList data={value} depth={depth + 1} />
  }

  return <span className="text-text-dim">{fmtUnknown(value)}</span>
}

function PairList({ data, depth }: { data: Record<string, unknown>; depth: number }) {
  const entries = Object.entries(data)
  if (entries.length === 0) return <p className="text-[13px] text-text-mute">Пусто</p>

  return (
    <dl className="flex flex-col gap-1.5">
      {entries.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-2 text-[13px]">
          <dt className="truncate text-text-mute">{key}</dt>
          <dd className="min-w-0 break-words text-text-dim">
            <ValueView value={value} depth={depth} />
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function GenericBlock({ block }: { block: CardBlock }) {
  return (
    <BlockFrame title={block.title}>
      <ValueView value={block.data} depth={0} />
    </BlockFrame>
  )
}
