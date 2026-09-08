/**
 * Липкая панель действий внизу карточки.
 *
 * Кнопки — из `actions` сущности. Нажатие раскрывает форму; после успеха
 * карточка перерисовывается ответом сервера, а панель остаётся открытой, чтобы
 * диспетчер увидел новый статус (spec §9).
 */
import { useState } from 'react'
import type { ActionDef, AppMeta } from '@/shared/api/types'
import { Button, toButtonKind } from '@/shared/ui/Button'
import { ActionFormRenderer } from './actionRegistry'
import type { FormValues } from './buildZodSchema'

export interface ActionBarProps {
  actions: ActionDef[]
  meta: AppMeta
  onRun: (code: string, values: FormValues) => Promise<unknown>
  pending?: boolean
  error?: unknown
}

/**
 * При смене сущности состояние сбрасывается через `key` на стороне вызывающего
 * (см. PredictionCard и OrderCard), а не эффектом внутри: это дешевле и не
 * вызывает лишнего рендера.
 */
export function ActionBar({ actions, meta, onRun, pending, error }: ActionBarProps) {
  const [openCode, setOpenCode] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  if (actions.length === 0) {
    return (
      <div className="border-t border-line bg-panel px-3 py-2.5 text-[12px] text-text-mute">
        Доступных действий нет — сущность в конечном статусе.
      </div>
    )
  }

  const open = actions.find((a) => a.code === openCode)

  return (
    <div className="sticky bottom-0 border-t border-line bg-panel px-3 py-2.5">
      <div className="flex flex-wrap gap-1.5">
        {actions.map((action) => (
          <Button
            key={action.code}
            kind={openCode === action.code ? 'secondary' : toButtonKind(action.kind)}
            size="sm"
            aria-expanded={openCode === action.code}
            onClick={() => setOpenCode(openCode === action.code ? null : action.code)}
          >
            {action.label}
          </Button>
        ))}
      </div>

      {open ? (
        <div className="mt-3">
          <ActionFormRenderer
            action={open}
            meta={meta}
            pending={pending}
            onCancel={() => setOpenCode(null)}
            onSubmit={(values) => {
              void onRun(open.code, values).then(() => {
                setOpenCode(null)
                setDone(open.label)
              })
            }}
          />
        </div>
      ) : null}

      <div aria-live="polite" className="mt-2 empty:mt-0">
        {done ? <p className="text-[12px] text-risk-low">Выполнено: {done.toLowerCase()}</p> : null}
        {error ? (
          <p className="text-[12px] text-risk-high">
            Действие не выполнено: {error instanceof Error ? error.message : 'ошибка запроса'}
          </p>
        ) : null}
      </div>
    </div>
  )
}
