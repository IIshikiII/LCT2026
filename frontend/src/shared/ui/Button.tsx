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
  primary: 'bg-[#2f5f8a] text-text border-[#3d729f] hover:bg-[#37709f]',
  secondary: 'bg-raised text-text border-line-strong hover:bg-[#2d3740]',
  danger: 'bg-[#7a2a27] text-text border-[#9a3733] hover:bg-[#8d312e]',
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
