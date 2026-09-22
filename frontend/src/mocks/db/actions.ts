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

/**
 * Ключ справочника уровней риска. Форма решения диспетчера берёт варианты
 * оттуда же, откуда причины: фронт резолвит любой `optionsRef` через
 * `meta.reasons`, и отдельный механизм ради одного списка не нужен.
 */
export const RISK_LEVELS_REF = 'riskLevels'

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
export function predictionActions(status: string, assignee?: string): ActionDef[] {
  if (status === 'NEW') {
    return [
      {
        code: 'take',
        label: 'Взять в работу',
        kind: 'secondary',
        fields: [],
      },
      decideAction(),
    ]
  }

  if (status === 'IN_REVIEW') {
    return [
      decideAction(),
      {
        code: 'release',
        label: 'Вернуть в очередь',
        kind: 'ghost',
        help: assignee ? `Сейчас за прогнозом закреплён ${assignee}` : undefined,
        fields: [],
      },
    ]
  }

  return []
}

/**
 * Единственное решение диспетчера по прогнозу (ADR 0006).
 *
 * Диспетчер отвечает на один вопрос: верен ли уровень. Нужен ли выезд, решает
 * итоговый уровень, а не согласие. Поэтому действие одно, а не два.
 */
function decideAction(): ActionDef {
  return {
    code: 'decide',
    label: 'Принять решение',
    kind: 'primary',
    fields: [
      {
        name: 'dispatcherLevel',
        label: 'Уровень по решению диспетчера',
        type: 'select',
        required: true,
        optionsRef: RISK_LEVELS_REF,
        help: 'Высокий и критический требуют выезда: система создаст заявку.',
      },
      {
        name: 'reason',
        label: 'Причина изменения уровня',
        type: 'select',
        optionsRef: REJECTION_REASONS_REF,
        help: 'Заполняется, когда уровень отличается от предложенного моделью',
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
  }
}

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
            name: 'factConfirmed',
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
