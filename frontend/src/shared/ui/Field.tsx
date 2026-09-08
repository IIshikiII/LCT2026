/**
 * Обёртка поля формы: подпись, признак обязательности, контрол, подсказка,
 * ошибка. Используется динамической формой действий (ADR 0004) и ничем больше.
 */
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { cn } from '@/shared/lib/cn'

export interface FieldProps {
  id: string
  label: string
  required?: boolean
  help?: ReactNode
  error?: string
  children: ReactNode
}

export function Field({ id, label, required, help, error, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[12px] text-text-dim">
        {label}
        {required ? (
          <span className="ml-0.5 text-risk-high" title="Обязательное поле">
            *
          </span>
        ) : null}
      </label>
      {children}
      {help && !error ? <p className="text-[12px] text-text-mute">{help}</p> : null}
      {error ? (
        <p role="alert" className="text-[12px] text-risk-high">
          {error}
        </p>
      ) : null}
    </div>
  )
}

const controlBase =
  'w-full rounded border border-line bg-sunken px-2 py-1.5 text-[13px] text-text ' +
  'outline-offset-1 placeholder:text-text-mute ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong ' +
  'disabled:opacity-50'

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(controlBase, 'h-8', className)} {...rest} />
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea rows={3} className={cn(controlBase, 'resize-y', className)} {...rest} />
}

export function Select({ className, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(controlBase, 'h-8', className)} {...rest} />
}
