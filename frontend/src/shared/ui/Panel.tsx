/**
 * Панель — базовая единица компоновки. Рамка, фон `--panel`, радиус 4 px.
 * Теней нет: глубина передаётся фоном (docs/05-ui-kit.md).
 */
import { useId, type ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

export interface PanelProps {
  title?: ReactNode
  /** Правый верхний угол заголовка: кнопки, счётчики, ссылки. */
  actions?: ReactNode
  children: ReactNode
  className?: string
  /** Отступы внутри тела. Выключается для таблиц во всю ширину. */
  padded?: boolean
}

export function Panel({ title, actions, children, className, padded = true }: PanelProps) {
  // Заголовок связывается с секцией: панель становится ориентиром для программ
  // чтения с экрана, а не безымянным блоком.
  const titleId = useId()

  return (
    <section
      aria-labelledby={title ? titleId : undefined}
      className={cn('flex flex-col rounded border border-line bg-panel', className)}
    >
      {(title ?? actions) && (
        <header className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line px-3">
          <h2 id={titleId} className="truncate text-[13px] font-medium text-text">
            {title}
          </h2>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </header>
      )}
      <div className={cn('min-h-0 flex-1', padded && 'p-3')}>{children}</div>
    </section>
  )
}
