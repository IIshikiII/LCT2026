/**
 * Переключаемая метка фильтра. Один компонент на журнал, карту и заявки —
 * фильтры у них общие и живут в URL (docs/06-url-state.md).
 */
import { cn } from '@/shared/lib/cn'

export interface ChipProps {
  label: string
  active: boolean
  onToggle: () => void
  /** Цвет точки: уровень риска или направление. Необязателен. */
  color?: string
  count?: number
  title?: string
}

export function Chip({ label, active, onToggle, color, count, title }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      title={title ?? label}
      onClick={onToggle}
      className={cn(
        'inline-flex h-6 items-center gap-1.5 rounded border px-2 text-[12px]',
        'transition-colors duration-150 outline-offset-2',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
        active
          ? 'border-line-strong bg-raised text-text'
          : 'border-line bg-transparent text-text-dim hover:text-text',
      )}
    >
      {color ? (
        <span aria-hidden="true" className="size-1.5 rounded-full" style={{ background: color }} />
      ) : null}
      {label}
      {typeof count === 'number' ? <span className="mono text-text-mute">{count}</span> : null}
    </button>
  )
}
