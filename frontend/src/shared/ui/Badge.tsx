/**
 * Метка статуса. Нейтральная по умолчанию: цветом в этом интерфейсе кодируется
 * только риск, иначе шкала риска перестаёт читаться (docs/05-ui-kit.md).
 */
import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

export interface BadgeProps {
  children: ReactNode
  /** Цвет рамки и точки. Передаётся только там, где смысл именно в риске. */
  color?: string
  title?: string
  className?: string
}

export function Badge({ children, color, title, className }: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded border border-line bg-sunken',
        'px-1.5 py-0.5 text-[12px] whitespace-nowrap text-text-dim',
        className,
      )}
    >
      {color ? (
        <span
          aria-hidden="true"
          className="size-1.5 shrink-0 rounded-full"
          style={{ background: color }}
        />
      ) : null}
      {/* Подпись обрезается многоточием: «Несанкционированный доступ» шире
          своей колонки и без этого ложилась на соседнюю. */}
      <span className="truncate">{children}</span>
    </span>
  )
}
