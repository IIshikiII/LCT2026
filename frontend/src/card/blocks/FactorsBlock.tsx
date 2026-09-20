/**
 * Вклад признаков в прогноз — главный блок объяснимости.
 *
 * Полосы в обе стороны от нуля: вправо — признак повышает риск, влево — снижает.
 * Знак кодируется и положением, и цветом, и подписью значения, чтобы читалось
 * без цвета тоже. Ось зафиксирована на плюс-минус 1 (ADR 0011): вес приходит
 * уже приведённым к этому диапазону, поэтому карточки сравнимы друг с другом,
 * а не только строки внутри одной карточки.
 */
import { useState } from 'react'
import { z } from 'zod'
import type { CardBlock } from '@/shared/api/types'
import { BlockFrame } from './BlockFrame'
import { GenericBlock } from './GenericBlock'

const Schema = z.looseObject({
  items: z.array(
    z.looseObject({
      label: z.string(),
      weight: z.number(),
      value: z.string().optional(),
    }),
  ),
  note: z.string().optional(),
})

const VISIBLE_LIMIT = 6

export function FactorsBlock({ block }: { block: CardBlock }) {
  const [expanded, setExpanded] = useState(false)
  const parsed = Schema.safeParse(block.data)
  // Форма данных не совпала — деградируем до запасного рендерера, а не падаем.
  if (!parsed.success) return <GenericBlock block={block} />

  const items = parsed.data.items
  const visible = expanded ? items : items.slice(0, VISIBLE_LIMIT)
  const hidden = items.length - visible.length

  return (
    <BlockFrame title={block.title}>
      <ul className="flex flex-col gap-2">
        {visible.map((item) => {
          const clamped = Math.max(-1, Math.min(1, item.weight))
          const share = Math.abs(clamped) * 50
          const raises = clamped >= 0
          return (
            <li key={item.label} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-2 text-[13px]">
                <span className="min-w-0 truncate text-text-dim">{item.label}</span>
                {item.value ? (
                  <span className="mono shrink-0 text-[12px] text-text-mute">{item.value}</span>
                ) : null}
              </div>
              <div
                className="relative h-1.5 rounded-[1px] bg-sunken"
                role="img"
                aria-label={`${item.label}: ${raises ? 'повышает' : 'снижает'} риск`}
              >
                <span className="absolute inset-y-0 left-1/2 w-px bg-line" aria-hidden="true" />
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 rounded-[1px]"
                  style={{
                    width: `${share}%`,
                    left: raises ? '50%' : undefined,
                    right: raises ? undefined : '50%',
                    background: raises ? 'var(--risk-high)' : 'var(--risk-low)',
                  }}
                />
              </div>
            </li>
          )
        })}
      </ul>
      {hidden > 0 ? (
        <button
          type="button"
          className="mt-2 text-[12px] text-text-mute underline hover:text-text-dim"
          onClick={() => setExpanded(true)}
        >
          Ещё {hidden} {factorsWord(hidden)}
        </button>
      ) : null}
      {parsed.data.note ? (
        <p className="mt-2 text-[12px] text-text-mute">{parsed.data.note}</p>
      ) : null}
    </BlockFrame>
  )
}

/** Русское склонение слова «фактор» для счётчика скрытых строк. */
function factorsWord(count: number): string {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return 'фактор'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'фактора'
  return 'факторов'
}
