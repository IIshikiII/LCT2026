/**
 * Список пар «подпись — значение»: паспорт объекта, короткая сводка.
 */
import { z } from 'zod'
import type { CardBlock } from '@/shared/api/types'
import { fmtUnknown } from '@/shared/lib/format'
import { BlockFrame } from './BlockFrame'
import { GenericBlock } from './GenericBlock'

const Schema = z.looseObject({
  items: z.array(z.looseObject({ label: z.string(), value: z.unknown() })),
})

/** ISO-даты в паспорте печатаем датой, а не строкой из базы. */
function display(value: unknown): string {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    return new Date(value).toLocaleDateString('ru-RU')
  }
  return fmtUnknown(value)
}

export function KeyValueBlock({ block }: { block: CardBlock }) {
  const parsed = Schema.safeParse(block.data)
  if (!parsed.success) return <GenericBlock block={block} />

  return (
    <BlockFrame title={block.title}>
      <dl className="flex flex-col gap-1.5">
        {parsed.data.items.map((item) => (
          <div key={item.label} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 text-[13px]">
            <dt className="truncate text-text-mute">{item.label}</dt>
            <dd className="mono min-w-0 truncate text-text-dim">{display(item.value)}</dd>
          </div>
        ))}
      </dl>
    </BlockFrame>
  )
}
