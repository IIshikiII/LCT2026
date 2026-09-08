/**
 * Уровень риска кодируется дважды: цветом и вертикальной планкой 3 px слева.
 * Только цветом — нельзя: примерно 8 % мужчин не различат зелёный и красный.
 *
 * Цвет берётся из меты (`riskLevels[].colorVar`), а не из карты, захардкоженной
 * по кодам. Неизвестный уровень получает детерминированный цвет (ADR 0002).
 */
import type { AppMeta } from '@/shared/api/types'
import { cn } from '@/shared/lib/cn'
import { levelColor, levelLabel } from '@/shared/lib/risk'

export interface RiskBarProps {
  level: string
  meta: AppMeta
  /** Показать подпись уровня рядом с планкой. */
  withLabel?: boolean
  className?: string
}

export function RiskBar({ level, meta, withLabel = false, className }: RiskBarProps) {
  const color = levelColor(level, meta)
  const label = levelLabel(level, meta)

  return (
    <span className={cn('inline-flex items-center gap-2', className)} title={label}>
      <span
        aria-hidden="true"
        className="inline-block h-4 w-[3px] shrink-0 rounded-[1px]"
        style={{ background: color }}
      />
      {withLabel ? <span className="text-[13px] text-text-dim">{label}</span> : null}
      {!withLabel ? <span className="sr-only">{label}</span> : null}
    </span>
  )
}
