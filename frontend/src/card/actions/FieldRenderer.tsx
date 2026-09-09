/**
 * Отрисовка одного поля формы действия по его описанию из API.
 *
 * Тип поля — строка. Неизвестный тип рисуется текстовым вводом: форма остаётся
 * рабочей, даже если бэкенд опередил фронт (docs/03-extension-points.md).
 *
 * Опции для `select` берутся из `meta.reasons[field.optionsRef]` — справочник
 * меняется на бэкенде, фронт подхватывает без пересборки.
 */
import type { UseFormRegisterReturn } from 'react-hook-form'
import type { AppMeta, FieldDef } from '@/shared/api/types'
import { Field, Select, TextArea, TextInput } from '@/shared/ui/Field'

export interface FieldRendererProps {
  field: FieldDef
  meta: AppMeta
  register: UseFormRegisterReturn
  error?: string
  id: string
}

export function FieldRenderer({ field, meta, register, error, id }: FieldRendererProps) {
  if (field.type === 'boolean') {
    return (
      <label htmlFor={id} className="flex items-center gap-2 text-[13px] text-text-dim">
        <input
          id={id}
          type="checkbox"
          className="size-3.5 accent-[var(--accent-strong)]"
          aria-invalid={Boolean(error)}
          {...register}
        />
        {field.label}
        {error ? (
          <span role="alert" className="text-[12px] text-risk-high">
            {error}
          </span>
        ) : null}
      </label>
    )
  }

  const common = { id, 'aria-invalid': Boolean(error), ...register }

  return (
    <Field id={id} label={field.label} required={field.required} help={field.help} error={error}>
      {field.type === 'textarea' ? (
        <TextArea placeholder={field.placeholder} {...common} />
      ) : field.type === 'select' ? (
        <Select {...common}>
          <option value="">Не выбрано</option>
          {(meta.reasons[field.optionsRef ?? ''] ?? []).map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </Select>
      ) : field.type === 'datetime' ? (
        <TextInput type="datetime-local" {...common} />
      ) : (
        // text и всё, чего фронт пока не знает.
        <TextInput type="text" placeholder={field.placeholder} {...common} />
      )}
    </Field>
  )
}
