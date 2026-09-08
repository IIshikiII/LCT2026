/**
 * Состояния загрузки, пустоты и ошибки.
 *
 * Собраны в одном файле намеренно: это три вида одного и того же — «данных на
 * экране нет, и вот почему». Разводить их по трём файлам смысла нет.
 */
import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import { Button } from './Button'
import { Icon } from './Icon'

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Загрузка"
      className={cn(
        'inline-block size-4 animate-spin rounded-full border-2 border-line',
        'border-t-text-dim',
        className,
      )}
    />
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn('block animate-pulse rounded bg-raised', className)} />
}

/** Скелет таблицы: столько строк, сколько ожидается данных. */
export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-px p-3" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-7" />
      ))}
    </div>
  )
}

export interface EmptyStateProps {
  title: string
  /** Что конкретно поменять, чтобы данные появились. */
  hint?: ReactNode
  action?: ReactNode
}

export function EmptyState({ title, hint, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <p className="text-[14px] text-text">{title}</p>
      {hint ? <p className="max-w-md text-[13px] text-text-mute">{hint}</p> : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  )
}

export interface ErrorStateProps {
  title?: string
  error?: unknown
  onRetry?: () => void
}

export function ErrorState({ title = 'Не удалось загрузить данные', error, onRetry }: ErrorStateProps) {
  const detail = error instanceof Error ? error.message : undefined
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <Icon name="warning" size={20} className="text-risk-high" />
      <p className="text-[14px] text-text">{title}</p>
      {detail ? <p className="max-w-md text-[13px] text-text-mute">{detail}</p> : null}
      {onRetry ? (
        <Button size="sm" icon={<Icon name="refresh" />} onClick={onRetry} className="mt-1">
          Повторить
        </Button>
      ) : null}
    </div>
  )
}
