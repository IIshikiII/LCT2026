/**
 * Сборка «базы данных» заглушек.
 *
 * Собирается один раз и кэшируется по составу активных направлений: включили
 * новое в дев-панели — кэш сбрасывается сам, и данные пересобираются вместе с
 * ним. Никакой перезагрузки страницы не требуется.
 *
 * База изменяемая: действия над прогнозами и заявками меняют статусы прямо
 * здесь. Так полный цикл «прогноз → заявка → закрытие с разметкой» проходится
 * на моках по-настоящему, а не имитируется на клиенте.
 */
import type { AppMeta, ModelMetric, PipelineHealth } from '@/shared/api/types'
import { activeDirections } from '../directions'
import { buildMeta } from './meta'
import { buildModelMetrics, buildPipelineHealth } from './metrics'
import { buildOrders, type OrderRecord } from './orders'
import { buildPredictions, type PredictionRecord } from './predictions'
import { NOW, resetFaker } from './rng'

export interface Db {
  /** Зафиксированный момент «сейчас». Все относительные даты считаются от него. */
  now: Date
  meta: AppMeta
  predictions: PredictionRecord[]
  predictionById: Map<string, PredictionRecord>
  orders: OrderRecord[]
  orderById: Map<string, OrderRecord>
  metrics: ModelMetric[]
  pipeline: PipelineHealth
}

let cache: { key: string; value: Db } | null = null

function currentKey(): string {
  return activeDirections()
    .map((d) => d.meta.code)
    .join(',')
}

function build(): Db {
  resetFaker()

  const predictions = buildPredictions()
  // buildOrders проставляет прогнозам orderId и уточняет их статусы.
  const orders = buildOrders(predictions)

  return {
    now: NOW,
    meta: buildMeta(),
    predictions,
    predictionById: new Map(predictions.map((p) => [p.id, p])),
    orders,
    orderById: new Map(orders.map((o) => [o.id, o])),
    metrics: buildModelMetrics(),
    pipeline: buildPipelineHealth(predictions),
  }
}

export function db(): Db {
  const key = currentKey()
  if (!cache || cache.key !== key) {
    cache = { key, value: build() }
  }
  return cache.value
}

/** Сбросить состояние: тесты и кнопка «сбросить» в дев-панели. */
export function resetDb(): void {
  cache = null
}

export type { OrderRecord, PredictionRecord }
