/**
 * Метрики моделей и состояние конвейера — то, ради чего на дашборде существует
 * виджет соответствия метрикам ТЗ.
 *
 * Значения `quality` заданы в плагинах направлений так, чтобы виджет было видно
 * в обоих состояниях: часть направлений выше целевых, одно — ниже по Precision.
 * Иначе на защите непонятно, что виджет вообще умеет краснеть.
 */
import type { ModelMetric, PipelineHealth } from '@/shared/api/types'
import { activeDirections } from '../directions'
import { MAX_COMPUTE_MS, MIN_HORIZON_HOURS, type PredictionRecord } from './predictions'
import { NOW, hoursFrom, iso } from './rng'

/** Целевые значения из ТЗ. Приходят с бэкенда, чтобы фронт их не хардкодил. */
export const TARGET_PRECISION = 0.7
export const TARGET_RECALL = 0.5

export function buildModelMetrics(): ModelMetric[] {
  return activeDirections().map((plugin) => {
    const base = {
      direction: plugin.meta.code,
      targetPrecision: TARGET_PRECISION,
      targetRecall: TARGET_RECALL,
    }
    // Направление без замера приходит строкой без чисел, как у сервера.
    if ('note' in plugin.quality) {
      return { ...base, evaluatedAt: '', method: plugin.quality.method, note: plugin.quality.note }
    }
    return {
      ...base,
      precision: plugin.quality.precision,
      recall: plugin.quality.recall,
      evaluatedAt: iso(hoursFrom(NOW, -6)),
      method: 'offline_holdout',
    }
  })
}

export function buildPipelineHealth(predictions: PredictionRecord[]): PipelineHealth {
  const computeTimes = predictions.map((p) => p.computeMs)
  const horizons = predictions.map((p) => p.horizonHours)
  const lastRunAt = hoursFrom(NOW, -0.2)

  return {
    lastRunAt: iso(lastRunAt),
    lastRunMs: Math.max(...computeTimes, 0),
    freshnessMinutes: Math.round((NOW.getTime() - lastRunAt.getTime()) / 60_000),
    maxComputeMs: Math.max(...computeTimes, 0),
    minHorizonHours: Math.min(...horizons, MIN_HORIZON_HOURS),
    targetComputeMs: MAX_COMPUTE_MS,
    targetHorizonHours: MIN_HORIZON_HOURS,
    streamLagMs: 48_000,
    streamEvents: 212,
    targetStreamLagMs: 300_000,
  }
}
