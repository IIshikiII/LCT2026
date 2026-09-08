/**
 * Реестр действий — точка расширения №2 (docs/03-extension-points.md).
 *
 * Действия приходят из API и уходят в единый эндпоинт (ADR 0004). Обычное
 * действие добавляется ТОЛЬКО на бэкенде: фронт нарисует кнопку и форму по
 * `fields` сам.
 *
 * Реестр нужен единственно для случаев, когда действию требуется нестандартный
 * UI. Сейчас такой случай ровно один — закрытие заявки с обязательной отметкой
 * «прогноз подтвердился».
 */
import type { FC } from 'react'
import { CloseOrderForm } from './CloseOrderForm'
import { DynamicForm } from './DynamicForm'
import type { ActionFormProps } from './DynamicForm'

export type ActionForm = FC<ActionFormProps>

export const actionRegistry: Record<string, ActionForm> = {
  close: CloseOrderForm,
}

export function formForAction(code: string): ActionForm {
  return actionRegistry[code] ?? DynamicForm
}

/**
 * Рендерер формы действия — по образцу BlockRenderer: поиск в реестре спрятан
 * внутрь компонента, чтобы вызывающий код не создавал компонент во время
 * рендера.
 */
export function ActionFormRenderer(props: ActionFormProps) {
  const Form = actionRegistry[props.action.code] ?? DynamicForm
  return <Form {...props} />
}
