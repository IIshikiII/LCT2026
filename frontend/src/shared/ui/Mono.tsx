/**
 * Моноширинный фрагмент с tabular-nums: идентификаторы, время, числа в таблице.
 * Без него колонки чисел «пляшут» при обновлении раз в минуту.
 */
import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('mono', className)}>{children}</span>
}
