/**
 * Сборка схемы валидации по описанию полей действия.
 *
 * Направление проверки здесь противоположно разбору ответов: на чтение мы
 * терпимы (ADR 0006), на запись — строги. Отправить на сервер форму с пустым
 * обязательным полем нельзя.
 *
 * Неизвестный `type` поля ведёт себя как текстовое: форма остаётся
 * отправляемой, даже если бэкенд придумал новый тип раньше фронта.
 */
import { z } from 'zod'
import type { FieldDef } from '@/shared/api/types'

export type FormValues = Record<string, unknown>

function stringField(field: FieldDef): z.ZodType {
  const min = field.required ? Math.max(1, field.minLength ?? 1) : (field.minLength ?? 0)
  let schema = z.string()
  if (min > 0) {
    schema = schema.min(
      min,
      min === 1 ? 'Заполните поле' : `Не короче ${min} символов`,
    )
  }
  return field.required ? schema : schema.optional().or(z.literal(''))
}

export function buildZodSchema(fields: FieldDef[]): z.ZodType<FormValues, FormValues> {
  const shape: Record<string, z.ZodType> = {}

  for (const field of fields) {
    if (field.type === 'boolean') {
      // Обязательный чекбокс — это «поставьте галочку». Обязательный ВЫБОР из
      // двух вариантов чекбоксом не выражается: для него нужна своя форма,
      // см. actionRegistry и CloseOrderForm.
      shape[field.name] = field.required
        ? z.boolean().refine((v) => v === true, 'Требуется подтверждение')
        : z.boolean().optional()
      continue
    }

    if (field.type === 'select') {
      shape[field.name] = field.required
        ? z.string().min(1, 'Выберите значение')
        : z.string().optional().or(z.literal(''))
      continue
    }

    if (field.type === 'datetime') {
      shape[field.name] = field.required
        ? z.string().min(1, 'Укажите дату и время')
        : z.string().optional().or(z.literal(''))
      continue
    }

    // text, textarea и всё неизвестное.
    shape[field.name] = stringField(field)
  }

  return z.object(shape) as unknown as z.ZodType<FormValues, FormValues>
}

/** Значения по умолчанию: без них react-hook-form считает поля неуправляемыми. */
export function defaultValuesFor(fields: FieldDef[]): FormValues {
  const values: FormValues = {}
  for (const field of fields) {
    values[field.name] = field.type === 'boolean' ? false : ''
  }
  return values
}
