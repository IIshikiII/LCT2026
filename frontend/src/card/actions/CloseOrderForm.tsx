/**
 * Закрытие заявки — единственное действие с нестандартной формой.
 *
 * Причина ровно одна: отметка «прогноз подтвердился / не подтвердился» должна
 * быть обязательным выбором из двух вариантов, а не чекбоксом. Из неё считаются
 * Precision и Recall на реальных данных, и «диспетчер не тронул галочку» не
 * должно молча означать «не подтвердился» (ADR 0004).
 *
 * Всё остальное — фактическая причина и комментарий — берётся из `action.fields`,
 * то есть справочник причин по-прежнему живёт на бэкенде.
 */
import { useState } from 'react'
import { Button, toButtonKind } from '@/shared/ui/Button'
import { Field, Select, TextArea } from '@/shared/ui/Field'
import { Spinner } from '@/shared/ui/states'
import type { ActionFormProps } from './DynamicForm'

const MIN_COMMENT = 5

export function CloseOrderForm({ action, meta, onSubmit, onCancel, pending }: ActionFormProps) {
  const causeField = action.fields.find((f) => f.name === 'actualCause')
  const options = meta.reasons[causeField?.optionsRef ?? ''] ?? []

  const [actualCause, setActualCause] = useState('')
  const [confirmed, setConfirmed] = useState<'yes' | 'no' | ''>('')
  const [comment, setComment] = useState('')
  const [touched, setTouched] = useState(false)

  const causeError = touched && !actualCause ? 'Выберите фактическую причину' : undefined
  const confirmedError = touched && !confirmed ? 'Отметьте, подтвердился ли прогноз' : undefined
  const commentError =
    touched && comment.trim().length < MIN_COMMENT ? `Не короче ${MIN_COMMENT} символов` : undefined

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (!actualCause || !confirmed || comment.trim().length < MIN_COMMENT) return
    onSubmit({
      actualCause,
      predictionConfirmed: confirmed === 'yes',
      comment: comment.trim(),
    })
  }

  return (
    <form aria-label={action.label} className="flex flex-col gap-3" onSubmit={submit} noValidate>
      <Field id="close-cause" label="Фактическая причина" required error={causeError}>
        <Select
          id="close-cause"
          value={actualCause}
          aria-invalid={Boolean(causeError)}
          onChange={(event) => setActualCause(event.target.value)}
        >
          <option value="">Не выбрано</option>
          {options.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </Select>
      </Field>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-[12px] text-text-dim">
          Прогноз подтвердился
          <span className="ml-0.5 text-risk-high" title="Обязательное поле">
            *
          </span>
        </legend>
        <div className="flex gap-4">
          {(
            [
              ['yes', 'Да, событие подтвердилось'],
              ['no', 'Нет, тревога ложная'],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className="flex items-center gap-1.5 text-[13px] text-text-dim">
              <input
                type="radio"
                name="predictionConfirmed"
                value={value}
                checked={confirmed === value}
                onChange={() => setConfirmed(value)}
                className="size-3.5 accent-[var(--accent-strong)]"
              />
              {label}
            </label>
          ))}
        </div>
        {confirmedError ? (
          <p role="alert" className="text-[12px] text-risk-high">
            {confirmedError}
          </p>
        ) : null}
        <p className="text-[12px] text-text-mute">
          Эта отметка — источник качества модели на реальных данных.
        </p>
      </fieldset>

      <Field id="close-comment" label="Что сделано" required error={commentError}>
        <TextArea
          id="close-comment"
          value={comment}
          aria-invalid={Boolean(commentError)}
          onChange={(event) => setComment(event.target.value)}
        />
      </Field>

      <div className="flex items-center gap-2">
        <Button type="submit" kind={toButtonKind(action.kind)} disabled={pending}>
          {pending ? <Spinner /> : null}
          {action.label}
        </Button>
        <Button kind="ghost" onClick={onCancel} disabled={pending}>
          Отмена
        </Button>
      </div>
    </form>
  )
}
