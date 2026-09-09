/**
 * Кнопка. Четыре вида, два размера. Анимация — только на действие, 140 мс.
 *
 * `kind` приходит из ActionDef строкой, поэтому неизвестное значение
 * деградирует до `secondary`, а не роняет рендер (ADR 0006).
 */
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

export type ButtonKind = 'primary' | 'secondary' | 'danger' | 'ghost'

const kinds: Record<ButtonKind, string> = {
  primary: 'bg-accent text-text-on-accent border-accent-strong hover:bg-accent-hover',
  secondary: 'bg-raised text-text border-line-strong hover:bg-secondary-hover',
  danger: 'bg-danger text-text-on-accent border-danger-strong hover:bg-danger-hover',
  ghost: 'bg-transparent text-text-dim border-transparent hover:bg-raised hover:text-text',
}

/** Приводит произвольную строку из API к известному виду кнопки. */
export function toButtonKind(value: string | undefined): ButtonKind {
  return value === 'primary' || value === 'danger' || value === 'ghost' ? value : 'secondary'
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: ButtonKind
  size?: 'md' | 'sm'
  icon?: ReactNode
}

export function Button({
  kind = 'secondary',
  size = 'md',
  icon,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded border font-medium',
        'transition-colors duration-150 outline-offset-2',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
        'disabled:cursor-not-allowed disabled:opacity-45',
        size === 'sm' ? 'h-6 px-2 text-[12px]' : 'h-8 px-3 text-[13px]',
        kinds[kind],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  )
}
