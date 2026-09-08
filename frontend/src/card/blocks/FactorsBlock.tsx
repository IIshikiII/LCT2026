/**
 * Вклад признаков в прогноз — главный блок объяснимости.
 *
 * Полосы в обе стороны от нуля: вправо — признак повышает риск, влево — снижает.
 * Знак кодируется и положением, и цветом, и подписью значения, чтобы читалось
 * без цвета тоже.
 */
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
})

export function FactorsBlock({ block }: { block: CardBlock }) {
  const parsed = Schema.safeParse(block.data)
  // Форма данных не совпала — деградируем до запасного рендерера, а не падаем.
  if (!parsed.success) return <GenericBlock block={block} />

  const items = parsed.data.items
  const max = Math.max(...items.map((i) => Math.abs(i.weight)), 0.001)

  return (
    <BlockFrame title={block.title}>
      <ul className="flex flex-col gap-2">
        {items.map((item) => {
          const share = (Math.abs(item.weight) / max) * 50
          const raises = item.weight >= 0
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
    </BlockFrame>
  )
}
