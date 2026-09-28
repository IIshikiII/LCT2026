/**
 * Форма действия, собранная по описанию полей из API.
 *
 * Это путь по умолчанию для любого действия — включая то, о котором фронт
 * никогда не слышал (критерий приёмки spec §12). Отдельный компонент нужен
 * только там, где требуется нестандартный UI, см. actionRegistry.
 */
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm, useWatch } from 'react-hook-form'
import type { ActionDef, AppMeta } from '@/shared/api/types'
import { Button, toButtonKind } from '@/shared/ui/Button'
import { Spinner } from '@/shared/ui/states'
import { FieldRenderer } from './FieldRenderer'
import {
  buildZodSchema,
  defaultValuesFor,
  enabledValues,
  isFieldEnabled,
  type FormValues,
} from './buildZodSchema'

export interface ActionFormProps {
  action: ActionDef
  meta: AppMeta
  onSubmit: (values: FormValues) => void
  onCancel: () => void
  pending?: boolean
}

export function DynamicForm({ action, meta, onSubmit, onCancel, pending }: ActionFormProps) {
  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(buildZodSchema(action.fields)),
    defaultValues: defaultValuesFor(action.fields),
    mode: 'onSubmit',
  })
  // Значения нужны условиям доступности полей (`enabledWhen`).
  const values = useWatch({ control }) as FormValues

  return (
    <form
      // Имя формы совпадает с подписью действия: кнопка в панели и кнопка
      // отправки называются одинаково, и без имени формы их не различить —
      // ни программе чтения с экрана, ни тесту.
      aria-label={action.label}
      className="flex flex-col gap-3"
      onSubmit={handleSubmit((submitted) => onSubmit(enabledValues(action.fields, submitted)))}
      noValidate
    >
      {action.confirm ? (
        <p className="rounded border border-line bg-sunken px-2 py-1.5 text-[12px] text-text-dim">
          {action.confirm}
        </p>
      ) : null}

      {action.fields.map((field) => (
        <FieldRenderer
          key={field.name}
          id={`${action.code}-${field.name}`}
          field={field}
          meta={meta}
          register={register(field.name)}
          disabled={!isFieldEnabled(field, values)}
          error={errors[field.name]?.message as string | undefined}
        />
      ))}

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
