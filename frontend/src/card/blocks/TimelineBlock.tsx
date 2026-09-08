/**
 * События объекта на оси времени: срабатывания, выезды, ремонты, обследования,
 * доступы. Хронология сверху вниз, свежее — первым.
 */
import { z } from 'zod'
import type { CardBlock } from '@/shared/api/types'
import { fmtDateTime } from '@/shared/lib/format'
import { BlockFrame } from './BlockFrame'
import { GenericBlock } from './GenericBlock'

const Schema = z.looseObject({
  events: z.array(
    z.looseObject({
      at: z.string(),
      title: z.string(),
      kind: z.string(),
      note: z.string().optional(),
    }),
  ),
})

export function TimelineBlock({ block }: { block: CardBlock }) {
  const parsed = Schema.safeParse(block.data)
  if (!parsed.success) return <GenericBlock block={block} />

  const events = parsed.data.events

  if (events.length === 0) {
    return (
      <BlockFrame title={block.title}>
        <p className="text-[13px] text-text-mute">Событий не зафиксировано</p>
      </BlockFrame>
    )
  }

  return (
    <BlockFrame title={block.title}>
      <ol className="relative flex flex-col gap-3 border-l border-line pl-3">
        {events.map((event, index) => (
          <li key={`${event.at}-${index}`} className="relative">
            <span
              aria-hidden="true"
              className="absolute top-1.5 -left-[15px] size-1.5 rounded-full bg-line-strong"
            />
            <div className="mono text-[12px] text-text-mute">{fmtDateTime(event.at)}</div>
            <div className="text-[13px] text-text-dim">{event.title}</div>
            {event.note ? <div className="text-[12px] text-text-mute">{event.note}</div> : null}
          </li>
        ))}
      </ol>
    </BlockFrame>
  )
}
