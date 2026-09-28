/**
 * Направление прогнозирования как плагин.
 *
 * Это единственное место во всём проекте, где направления различаются. Всё
 * остальное — генератор данных, хендлеры, приложение — перебирает плагины и не
 * знает, сколько их (ADR 0002, ADR 0007).
 *
 * Добавить направление = один файл здесь + строка в ./index.ts. Правок за
 * пределами src/mocks/ не требуется — это и есть проверка архитектуры.
 */
import type { CardBlock, DirectionMeta, ReasonOption, Series } from '@/shared/api/types'
import type { Facility } from '../db/facilities'
import type { Rng } from '../db/rng'

/** Всё, что известно о прогнозе к моменту, когда плагин наполняет его содержимым. */
export interface PredictionSeed {
  id: string
  facility: Facility
  /** LOW | MEDIUM | HIGH | CRITICAL */
  level: string
  /** 0..1 */
  probability: number
  /** >= 24 по ТЗ */
  horizonHours: number
  computedAt: Date
  /** ГПСЧ, привязанный к id прогноза: содержимое карточки стабильно */
  rnd: Rng
}

export interface DirectionPlugin {
  meta: DirectionMeta
  /** Ключ справочника фактических причин в AppMeta.reasons. */
  reasonsRef: string
  reasons: ReasonOption[]
  /** Виды работ для автоматически создаваемой заявки. */
  workTypes: string[]
  /**
   * Качество модели по этому направлению. По ТЗ цели — Precision > 0.7 и
   * Recall > 0.5. Значения подобраны так, чтобы виджет метрик было видно и в
   * зелёном, и в красном состоянии (docs/04-mocks.md).
   */
  quality:
    | {
        precision: number
        recall: number
        /** наивное правило на той же выборке, с ним сравнивает дашборд */
        baseline?: { rule: string; precision: number; recall: number }
      }
    | { method: string; note: string }
  /** Относительная доля прогнозов этого направления в общем потоке. */
  share: number
  /** Каким объектам направление вообще применимо. */
  appliesTo(facility: Facility): boolean
  /** Одна строка человеческим языком — колонка «Прогноз» в журнале. */
  summary(seed: PredictionSeed): string
  /** Тело карточки: массив блоков, а не вёрстка (ADR 0003). */
  blocks(seed: PredictionSeed): CardBlock[]
  /** Ряды для GET /predictions/{id}/timeseries. */
  series(seed: PredictionSeed): Series[]
}
