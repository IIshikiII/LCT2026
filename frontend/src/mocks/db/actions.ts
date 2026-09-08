/**
 * Действия над прогнозами и заявками.
 *
 * Набор зависит от статуса, а не от направления, — поэтому строится здесь, а не
 * в плагинах направлений. Всё уходит в единый эндпоинт действий (ADR 0004):
 * фронт не знает, что означает код, он только рисует кнопку и форму по `fields`.
 *
 * Справочник причин закрытия у каждого направления свой: `optionsRef`
 * указывает на ключ в AppMeta.reasons.
 */
import type { ActionDef } from '@/shared/api/types'

/** Ключ общего справочника причин отклонения — один на все направления. */
export const REJECTION_REASONS_REF = 'rejection'

export const REJECTION_REASONS = [
  { code: 'KNOWN_ISSUE', label: 'Известная особенность объекта' },
  { code: 'PLANNED_WORKS', label: 'На объекте идут плановые работы' },
  { code: 'DUPLICATE', label: 'Дубль ранее обработанного прогноза' },
  { code: 'LOW_PRIORITY', label: 'Низкий приоритет, отложено' },
  { code: 'MODEL_ERROR', label: 'Ошибка модели' },
]

/**
 * Действия карточки прогноза.
 * `directionRef` — ключ справочника фактических причин этого направления.
 */
export function predictionActions(status: string): ActionDef[] {
  if (status === 'NEW' || status === 'IN_REVIEW') {
    return [
      {
        code: 'confirm_order',
        label: 'Подтвердить заявку',
        kind: 'primary',
        confirm: 'Заявка будет передана в работу. Подтвердить?',
        fields: [
          {
            name: 'comment',
            label: 'Комментарий диспетчера',
            type: 'textarea',
            placeholder: 'Необязательно',
          },
        ],
      },
      {
        code: 'inspect',
        // Этого кода нет в actionRegistry — он рисуется обычной динамической
        // формой. Ровно так работает добавление действия «только на бэкенде».
        label: 'Назначить осмотр',
        kind: 'secondary',
        fields: [
          { name: 'plannedAt', label: 'Срок осмотра', type: 'datetime', required: true },
          { name: 'crew', label: 'Бригада', type: 'text', required: true, minLength: 2 },
          { name: 'comment', label: 'Задача бригаде', type: 'textarea' },
        ],
      },
      {
        code: 'reject',
        label: 'Отклонить прогноз',
        kind: 'danger',
        confirm: 'Прогноз будет отклонён и уйдёт в статистику модели.',
        fields: [
          {
            name: 'reason',
            label: 'Причина отклонения',
            type: 'select',
            required: true,
            optionsRef: REJECTION_REASONS_REF,
          },
          {
            name: 'comment',
            label: 'Комментарий',
            type: 'textarea',
            required: true,
            minLength: 5,
            help: 'Уйдёт в обучающую выборку — пишите по существу.',
          },
        ],
      },
    ]
  }

  if (status === 'ORDER_CONFIRMED') {
    return [
      {
        code: 'reject',
        label: 'Отклонить прогноз',
        kind: 'danger',
        fields: [
          {
            name: 'reason',
            label: 'Причина отклонения',
            type: 'select',
            required: true,
            optionsRef: REJECTION_REASONS_REF,
          },
          { name: 'comment', label: 'Комментарий', type: 'textarea', required: true, minLength: 5 },
        ],
      },
    ]
  }

  return []
}

/**
 * Действия карточки заявки.
 * `causesRef` — справочник фактических причин направления, из которого заявка.
 */
export function orderActions(status: string, causesRef: string): ActionDef[] {
  if (status === 'AUTO_CREATED') {
    return [
      {
        code: 'confirm',
        label: 'Подтвердить заявку',
        kind: 'primary',
        fields: [
          { name: 'assignee', label: 'Ответственный', type: 'text', required: true, minLength: 2 },
          { name: 'comment', label: 'Комментарий', type: 'textarea' },
        ],
      },
      {
        code: 'reject',
        label: 'Отклонить заявку',
        kind: 'danger',
        confirm: 'Заявка будет отклонена.',
        fields: [
          {
            name: 'reason',
            label: 'Причина',
            type: 'select',
            required: true,
            optionsRef: REJECTION_REASONS_REF,
          },
          { name: 'comment', label: 'Комментарий', type: 'textarea', required: true, minLength: 5 },
        ],
      },
    ]
  }

  if (status === 'CONFIRMED') {
    return [
      {
        code: 'start',
        label: 'Взять в работу',
        kind: 'primary',
        fields: [{ name: 'crew', label: 'Бригада', type: 'text', required: true, minLength: 2 }],
      },
    ]
  }

  if (status === 'IN_PROGRESS') {
    return [
      {
        code: 'close',
        // Единственное действие с нестандартной формой (CloseOrderForm):
        // отметка «прогноз подтвердился» обязана быть радиовыбором, а не
        // чекбоксом. Из неё считается качество модели на реальных данных.
        label: 'Закрыть заявку',
        kind: 'primary',
        fields: [
          {
            name: 'actualCause',
            label: 'Фактическая причина',
            type: 'select',
            required: true,
            optionsRef: causesRef,
          },
          {
            name: 'predictionConfirmed',
            label: 'Прогноз подтвердился',
            type: 'boolean',
            required: true,
          },
          {
            name: 'comment',
            label: 'Что сделано',
            type: 'textarea',
            required: true,
            minLength: 5,
          },
        ],
      },
    ]
  }

  return []
}
