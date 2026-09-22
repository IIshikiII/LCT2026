/**
 * Генератор прогнозов.
 *
 * Перебирает активные направления и раздаёт им подходящие объекты. Сколько
 * направлений — не знает: берёт список из ../directions.
 *
 * Инварианты, которые проверяет src/mocks/seed.test.ts:
 *  - у всех прогнозов horizonHours >= 24 (метрика ТЗ);
 *  - computeMs < 5 минут (метрика ТЗ);
 *  - представлены все активные направления и все уровни риска.
 */
import type { Prediction, PredictionDetail } from '@/shared/api/types'
import { activeDirections, type DirectionPlugin } from '../directions'
import { experimentalBlock } from '../directions/blockKit'
import { predictionActions } from './actions'
import { FACILITIES, toFacilityRef, type Facility } from './facilities'
import { NOW, hoursFrom, iso, makeRng, type Rng } from './rng'

/** Всего прогнозов в сиде, spec §10. */
export const TOTAL_PREDICTIONS = 260

/** Метрика ТЗ: время формирования прогноза < 5 минут. */
export const MAX_COMPUTE_MS = 5 * 60_000

/** Метрика ТЗ: горизонт не менее 24 часов. */
export const MIN_HORIZON_HOURS = 24

const LEVELS = [
  { code: 'LOW', share: 0.4, probability: [0.05, 0.3] },
  { code: 'MEDIUM', share: 0.3, probability: [0.3, 0.55] },
  { code: 'HIGH', share: 0.2, probability: [0.55, 0.78] },
  { code: 'CRITICAL', share: 0.1, probability: [0.78, 0.97] },
] as const

const STATUSES = ['NEW', 'NEW', 'NEW', 'IN_REVIEW', 'ORDER_OPEN', 'DECIDED', 'CLOSED_CONFIRMED']

/** Диспетчеры смены. Настоящий список придёт из каталога организации. */
export const DISPATCHERS = ['Авдеев А.', 'Белова М.', 'Гущин П.', 'Орлова К.']

/** Имя, которым подписываются действия в заглушке. */
export const MOCK_DISPATCHER = DISPATCHERS[0] as string

/**
 * Решение диспетчера, согласованное со статусом.
 *
 * Статус и решение обязаны сходиться: `DECIDED` без вердикта или `NEW` с
 * исполнителем показали бы на демо состояние, которого сервер не создаёт.
 */
function verdictFor(status: string, level: string, rnd: Rng) {
  if (status === 'NEW') return { status }

  const assignee = rnd.pick(DISPATCHERS)
  if (status === 'IN_REVIEW') return { status, assignee }

  // Остальные статусы наступают только после решения. Уровень диспетчера
  // совпадает со статусом: заявка живёт на высоком и критическом.
  const needsOrder = status === 'ORDER_OPEN' || status.startsWith('CLOSED_')
  const dispatcherLevel = needsOrder
    ? rnd.pick(['HIGH', 'CRITICAL'])
    : rnd.pick(['LOW', 'MEDIUM'])
  return {
    status,
    assignee,
    dispatcherLevel,
    verdict: dispatcherLevel === level ? 'AGREED' : 'CORRECTED',
    decidedAt: iso(hoursFrom(NOW, -rnd.int(1, 12))),
  }
}

/** Уровень по позиции внутри направления — распределение точное, а не случайное. */
function levelAt(index: number, total: number) {
  const position = index / total
  let acc = 0
  for (const level of LEVELS) {
    acc += level.share
    if (position < acc) return level
  }
  return LEVELS[LEVELS.length - 1] as (typeof LEVELS)[number]
}

export interface PredictionRecord extends Prediction {
  /** Плагин направления — нужен, чтобы построить карточку по запросу. */
  plugin: DirectionPlugin
  facilityRecord: Facility
}

function buildForDirection(
  plugin: DirectionPlugin,
  count: number,
  startIndex: number,
): PredictionRecord[] {
  const suitable = FACILITIES.filter((f) => plugin.appliesTo(f))
  if (suitable.length === 0) return []

  return Array.from({ length: count }, (_, i) => {
    const id = `P-${String(startIndex + i + 1).padStart(4, '0')}`
    const rnd = makeRng(id)
    const facility = suitable[(i * 7 + startIndex) % suitable.length] as Facility
    const level = levelAt(i, count)
    const probability = rnd.float(level.probability[0], level.probability[1], 3)

    return {
      id,
      direction: plugin.meta.code,
      level: level.code,
      probability,
      // Горизонт всегда не меньше минимального по ТЗ.
      horizonHours: MIN_HORIZON_HOURS + rnd.int(0, 96),
      computedAt: iso(hoursFrom(NOW, -rnd.int(0, 20) - rnd.next())),
      // Разброс правдоподобный, но всегда ниже пяти минут.
      computeMs: rnd.int(12_000, MAX_COMPUTE_MS - 20_000),
      ...verdictFor(rnd.pick(STATUSES), level.code, rnd),
      facility: toFacilityRef(facility),
      facilityRecord: facility,
      summary: '',
      plugin,
    }
  }).map((record) => ({
    ...record,
    summary: plugin.summary({
      id: record.id,
      facility: record.facilityRecord,
      level: record.level,
      probability: record.probability,
      horizonHours: record.horizonHours,
      computedAt: new Date(record.computedAt),
      rnd: makeRng(`${record.id}-summary`),
    }),
  }))
}

export function buildPredictions(): PredictionRecord[] {
  const plugins = activeDirections()
  const shareSum = plugins.reduce((sum, p) => sum + p.share, 0)

  const records: PredictionRecord[] = []
  let cursor = 0
  for (const plugin of plugins) {
    const count = Math.round((plugin.share / shareSum) * TOTAL_PREDICTIONS)
    records.push(...buildForDirection(plugin, count, cursor))
    cursor += count
  }

  // Свежие сверху — так журнал открывается на актуальном.
  return records.sort((a, b) => (a.computedAt < b.computedAt ? 1 : -1))
}

/** Контекст для плагина. Одинаков для карточки и для рядов — карточка стабильна. */
function seedOf(record: PredictionRecord) {
  return {
    id: record.id,
    facility: record.facilityRecord,
    level: record.level,
    probability: record.probability,
    horizonHours: record.horizonHours,
    computedAt: new Date(record.computedAt),
    rnd: makeRng(`${record.id}-detail`),
  }
}

/**
 * Карточка строится по запросу, а не при сборке сида: 260 прогнозов с рядами
 * телеметрии — это лишние сотни тысяч точек в памяти без всякой пользы.
 */
export function buildDetail(record: PredictionRecord): PredictionDetail {
  const seed = seedOf(record)
  const blocks = record.plugin.blocks(seed)

  // Примерно каждому шестому прогнозу добавляем блок неизвестного фронту типа —
  // на демо видно, что GenericBlock работает (критерий приёмки spec §12).
  if (makeRng(`${record.id}-experimental`).bool(0.18)) {
    blocks.push(experimentalBlock(makeRng(`${record.id}-exp-data`)))
  }

  const { plugin: _plugin, facilityRecord: _facility, ...prediction } = record
  return {
    ...prediction,
    blocks,
    actions: predictionActions(record.status, record.assignee),
  }
}

export function buildSeries(record: PredictionRecord) {
  const seed = seedOf(record)
  return { series: record.plugin.series(seed), markerAt: record.computedAt }
}
