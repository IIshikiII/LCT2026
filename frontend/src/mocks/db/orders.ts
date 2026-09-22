/**
 * Заявки на превентивное обслуживание — модуль автоматического формирования
 * заявок из описания итогового продукта по ТЗ.
 *
 * Заявка всегда порождена прогнозом: сначала система создаёт её сама
 * (`AUTO_CREATED`), дальше её ведёт диспетчер. Терминальный статус заявки —
 * `DONE`, у прогноза — `CLOSED`: это разные сущности с разным жизненным циклом.
 * Закрытие несёт разметку `outcome.factConfirmed` — из неё считается
 * качество модели на реальных данных, поэтому у выполненных заявок она есть
 * всегда.
 */
import type { WorkOrder } from '@/shared/api/types'
import { orderActions } from './actions'
import { toFacilityRef } from './facilities'
import type { PredictionRecord } from './predictions'
import { hoursFrom, iso, makeRng } from './rng'

/** Сколько заявок в сиде, spec §10. */
export const TOTAL_ORDERS = 90

const STATUS_WEIGHTS: [string, number][] = [
  ['AUTO_CREATED', 0.25],
  ['CONFIRMED', 0.3],
  ['IN_PROGRESS', 0.2],
  ['CLOSED_CONFIRMED', 0.18],
  ['CLOSED_NOT_CONFIRMED', 0.05],
  ['REJECTED', 0.02],
]

function statusAt(index: number, total: number): string {
  const position = index / total
  let acc = 0
  for (const [code, share] of STATUS_WEIGHTS) {
    acc += share
    if (position < acc) return code
  }
  return 'AUTO_CREATED'
}

export interface OrderRecord extends WorkOrder {
  /** Ключ справочника фактических причин — нужен форме закрытия. */
  causesRef: string
  direction: string
}

/**
 * Создаёт заявки и проставляет прогнозам `orderId`.
 * Мутирует переданные записи прогнозов — связь двусторонняя, как и в жизни.
 */
export function buildOrders(predictions: PredictionRecord[]): OrderRecord[] {
  const orders: OrderRecord[] = []
  const step = Math.max(1, Math.floor(predictions.length / TOTAL_ORDERS))

  for (let i = 0; i < TOTAL_ORDERS; i += 1) {
    const prediction = predictions[(i * step) % predictions.length]
    if (!prediction || prediction.orderId) continue

    const id = `WO-${String(i + 1).padStart(4, '0')}`
    const rnd = makeRng(id)
    const status = statusAt(i, TOTAL_ORDERS)
    const createdAt = hoursFrom(new Date(prediction.computedAt), rnd.int(0, 2))
    // Срок — внутри горизонта прогноза; часть заявок намеренно просрочена,
    // иначе колонка «срок» на демо выглядит бессмысленной.
    const dueAt = hoursFrom(createdAt, rnd.int(-18, prediction.horizonHours))

    const outcome =
      status === 'CLOSED_CONFIRMED' || status === 'CLOSED_NOT_CONFIRMED'
        ? {
            actualCause: rnd.pick(prediction.plugin.reasons).code,
            // Факт подтверждён ровно там, где это говорит статус заявки: два
            // источника одной истины разошлись бы на первой правке.
            factConfirmed: status === 'CLOSED_CONFIRMED',
            comment: rnd.pick([
              'Дефект устранён на месте.',
              'Оборудование заменено, объект в работе.',
              'Замечаний не выявлено, объект оставлен в наблюдении.',
              'Выполнена чистка и калибровка.',
            ]),
            closedAt: iso(hoursFrom(dueAt, -rnd.int(0, 12))),
          }
        : undefined

    // Происхождение заявки: конвейер по порогу или решение диспетчера.
    // Состояние у обеих одно, различает их только это поле (ADR 0006).
    const createdBy = rnd.bool(0.25) ? 'DISPATCHER' : 'PIPELINE'

    prediction.orderId = id
    // Заявка закрывает прогноз своим исходом. ADR 0006.
    if (status === 'CLOSED_CONFIRMED' || status === 'CLOSED_NOT_CONFIRMED') {
      prediction.status = status
    } else if (status !== 'REJECTED' && prediction.status === 'NEW') {
      prediction.status = 'ORDER_OPEN'
    }

    orders.push({
      id,
      createdBy,
      number: `${String(2026)}-${String(i + 1).padStart(4, '0')}`,
      predictionId: prediction.id,
      facility: toFacilityRef(prediction.facilityRecord),
      workType: rnd.pick(prediction.plugin.workTypes),
      dueAt: iso(dueAt),
      status,
      createdAt: iso(createdAt),
      direction: prediction.direction,
      causesRef: prediction.plugin.reasonsRef,
      actions: orderActions(status, prediction.plugin.reasonsRef),
      outcome,
    })
  }

  return orders.sort((a, b) => (a.dueAt < b.dueAt ? -1 : 1))
}

/** Заявка в том виде, в каком её отдаёт API: без служебных полей сида. */
export function toWorkOrder(record: OrderRecord): WorkOrder {
  const { causesRef: _causesRef, direction: _direction, ...order } = record
  return order
}
