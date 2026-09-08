/**
 * Проверка сида заглушек.
 *
 * Здесь держатся две вещи: метрики ТЗ, которые нельзя нарушить даже в
 * демо-данных (горизонт >= 24 ч, время расчёта < 5 мин), и полнота покрытия по
 * всем четырём направлениям — иначе на защите какое-нибудь из них окажется
 * пустым.
 */
import { describe, expect, it } from 'vitest'
import { COLLECTORS, DISTRICTS, SECTIONS } from './db/catalog'
import { FACILITIES } from './db/facilities'
import { db } from './db'
import { MAX_COMPUTE_MS, MIN_HORIZON_HOURS, buildDetail } from './db/predictions'
import { BASE_DIRECTIONS } from './directions'
import { devFlags } from './devFlags'
import { TARGET_PRECISION, TARGET_RECALL } from './db/metrics'

describe('справочники', () => {
  it('содержат объёмы из спецификации', () => {
    expect(COLLECTORS).toHaveLength(8)
    expect(DISTRICTS).toHaveLength(5)
    expect(SECTIONS.length).toBeGreaterThanOrEqual(80)
    expect(SECTIONS.length).toBeLessThanOrEqual(105)
    expect(FACILITIES.length).toBeGreaterThanOrEqual(450)
    expect(FACILITIES.length).toBeLessThanOrEqual(750)
  })

  it('раскладывает объекты в пределах Москвы', () => {
    for (const f of FACILITIES) {
      expect(f.lat).toBeGreaterThan(55.5)
      expect(f.lat).toBeLessThan(56.0)
      expect(f.lon).toBeGreaterThan(37.2)
      expect(f.lon).toBeLessThan(37.95)
    }
  })
})

describe('прогнозы', () => {
  it('детерминированы: повторная сборка даёт те же данные', () => {
    const first = db().predictions.map((p) => `${p.id}:${p.probability}:${p.facility.id}`)
    const second = db().predictions.map((p) => `${p.id}:${p.probability}:${p.facility.id}`)
    expect(second).toEqual(first)
  })

  it('соблюдают горизонт из ТЗ: не меньше 24 часов', () => {
    for (const p of db().predictions) {
      expect(p.horizonHours).toBeGreaterThanOrEqual(MIN_HORIZON_HOURS)
    }
  })

  it('соблюдают время расчёта из ТЗ: меньше 5 минут', () => {
    for (const p of db().predictions) {
      expect(p.computeMs).toBeLessThan(MAX_COMPUTE_MS)
    }
  })

  it('покрывают все четыре направления из ТЗ', () => {
    const present = new Set(db().predictions.map((p) => p.direction))
    for (const plugin of BASE_DIRECTIONS) {
      expect(present).toContain(plugin.meta.code)
    }
  })

  it('покрывают все уровни риска, включая критический', () => {
    const levels = new Set(db().predictions.map((p) => p.level))
    expect([...levels].sort()).toEqual(['CRITICAL', 'HIGH', 'LOW', 'MEDIUM'])
  })

  it('дают каждому направлению непустую карточку с блоками', () => {
    for (const plugin of BASE_DIRECTIONS) {
      const record = db().predictions.find((p) => p.direction === plugin.meta.code)
      expect(record, `нет прогнозов направления ${plugin.meta.code}`).toBeDefined()
      const detail = buildDetail(record!)
      expect(detail.blocks.length).toBeGreaterThan(2)
      for (const block of detail.blocks) {
        expect(block.type).toBeTruthy()
        expect(block.title).toBeTruthy()
      }
    }
  })

  it('содержат блоки неизвестного фронту типа — проверка запасного рендерера', () => {
    const types = new Set(
      db()
        .predictions.slice(0, 60)
        .flatMap((p) => buildDetail(p).blocks.map((b) => b.type)),
    )
    expect(types).toContain('unknown-experimental')
  })
})

describe('заявки', () => {
  it('представлены во всех статусах', () => {
    const statuses = new Set(db().orders.map((o) => o.status))
    for (const code of ['AUTO_CREATED', 'CONFIRMED', 'IN_PROGRESS', 'CLOSED', 'REJECTED']) {
      expect(statuses).toContain(code)
    }
  })

  it('у закрытых всегда есть разметка «прогноз подтвердился»', () => {
    const closed = db().orders.filter((o) => o.status === 'CLOSED')
    expect(closed.length).toBeGreaterThan(0)
    for (const order of closed) {
      expect(order.outcome).toBeDefined()
      expect(typeof order.outcome?.predictionConfirmed).toBe('boolean')
      expect(order.outcome?.actualCause).toBeTruthy()
    }
  })

  it('всегда связаны с существующим прогнозом', () => {
    for (const order of db().orders) {
      expect(db().predictionById.has(order.predictionId)).toBe(true)
    }
  })
})

describe('метрики моделей', () => {
  it('заданы по каждому активному направлению', () => {
    expect(db().metrics).toHaveLength(BASE_DIRECTIONS.length)
  })

  it('видны и в зелёном, и в красном состоянии — иначе виджет нечем показать', () => {
    const metrics = db().metrics
    const green = metrics.filter((m) => m.precision >= TARGET_PRECISION && m.recall >= TARGET_RECALL)
    const red = metrics.filter((m) => m.precision < TARGET_PRECISION || m.recall < TARGET_RECALL)
    expect(green.length).toBeGreaterThan(0)
    expect(red.length).toBeGreaterThan(0)
  })

  it('конвейер укладывается в метрики ТЗ', () => {
    const { pipeline } = db()
    expect(pipeline.maxComputeMs).toBeLessThan(pipeline.targetComputeMs)
    expect(pipeline.minHorizonHours).toBeGreaterThanOrEqual(pipeline.targetHorizonHours)
  })
})

describe('пятое направление', () => {
  it('добавляется в мету и в данные одним флагом, без правок кода', () => {
    expect(db().meta.directions.map((d) => d.code)).not.toContain('FLOOD_RISK')

    devFlags.override({ extraDirection: true })

    expect(db().meta.directions.map((d) => d.code)).toContain('FLOOD_RISK')
    expect(db().predictions.some((p) => p.direction === 'FLOOD_RISK')).toBe(true)
    expect(db().metrics.some((m) => m.direction === 'FLOOD_RISK')).toBe(true)
  })
})
