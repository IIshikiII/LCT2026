/**
 * Здесь проверяется механизм, из-за которого пятое направление появляется в
 * интерфейсе само (ADR 0002). Если этот файл покраснел — сломан главный
 * архитектурный принцип, а не мелочь.
 */
import { describe, expect, it } from 'vitest'
import type { AppMeta } from '@/shared/api/types'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import {
  directionOptions,
  findDirection,
  findRiskLevel,
  findStatus,
  isTerminalStatus,
  levelColor,
  levelsBySeverity,
  sortedLevels,
  statusColor,
  statusOptions,
  synthColor,
} from './risk'

const meta: AppMeta = {
  ...FALLBACK_META,
  directions: [
    { code: 'ALPHA', label: 'Альфа', shortLabel: 'АЛ', accent: '#123456', minHorizonHours: 24 },
  ],
  // Порядок в массиве намеренно перепутан: сортировка идёт по полю order.
  riskLevels: [
    { code: 'HIGH', label: 'Высокий', colorVar: '--risk-high', order: 3 },
    { code: 'LOW', label: 'Низкий', colorVar: '--risk-low', order: 1 },
    { code: 'CRITICAL', label: 'Критический', colorVar: '--risk-critical', order: 4 },
    { code: 'MEDIUM', label: 'Средний', colorVar: '--risk-medium', order: 2 },
  ],
}

describe('findDirection', () => {
  it('отдаёт описание известного направления', () => {
    expect(findDirection('ALPHA', meta).label).toBe('Альфа')
  })

  it('синтезирует описание для неизвестного кода, а не падает', () => {
    const d = findDirection('OMEGA_RISK', meta)
    expect(d.label).toBe('OMEGA_RISK')
    expect(d.shortLabel).toBe('OR')
    expect(d.minHorizonHours).toBe(24)
    expect(d.accent).toMatch(/^hsl\(/)
  })
})

describe('synthColor', () => {
  it('детерминирован — один код всегда даёт один цвет', () => {
    expect(synthColor('OMEGA')).toBe(synthColor('OMEGA'))
    expect(synthColor('OMEGA')).not.toBe(synthColor('SIGMA'))
  })
})

describe('directionOptions', () => {
  it('достраивает список направлений кодами, встреченными в данных', () => {
    const codes = directionOptions(meta, ['ALPHA', 'OMEGA']).map((d) => d.code)
    expect(codes).toEqual(['ALPHA', 'OMEGA'])
  })

  it('работает на запасной мете, где направлений нет вовсе', () => {
    const codes = directionOptions(FALLBACK_META, ['OMEGA', 'OMEGA', 'SIGMA']).map((d) => d.code)
    expect(codes).toEqual(['OMEGA', 'SIGMA'])
  })
})

describe('уровни риска', () => {
  it('сортируются по полю order, а не по позиции в массиве', () => {
    expect(sortedLevels(meta).map((l) => l.code)).toEqual(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])
    expect(levelsBySeverity(meta)[0]?.code).toBe('CRITICAL')
  })

  it('превращают colorVar в CSS-значение', () => {
    expect(levelColor('HIGH', meta)).toBe('var(--risk-high)')
  })

  it('дают синтезированный цвет неизвестному уровню', () => {
    expect(findRiskLevel('EXTREME', meta).label).toBe('EXTREME')
    expect(levelColor('EXTREME', meta)).toMatch(/^hsl\(/)
  })
})

describe('статусы', () => {
  it('различают статусы прогнозов и заявок по scope', () => {
    // REJECTED — единственный код, общий для обеих сущностей. Терминальные
    // статусы различаются кодом: у заявки DONE, у прогноза CLOSED.
    expect(findStatus('REJECTED', 'order', FALLBACK_META).label).toBe('Отклонена')
    expect(findStatus('REJECTED', 'prediction', FALLBACK_META).label).toBe('Отклонён')
    expect(findStatus('DONE', 'order', FALLBACK_META).label).toBe('Выполнена')
    expect(findStatus('CLOSED', 'prediction', FALLBACK_META).label).toBe('Закрыт')
  })

  it('подставляют код вместо подписи для неизвестного статуса', () => {
    expect(findStatus('WAT', 'order', FALLBACK_META).label).toBe('WAT')
  })

  it('берут цвет метки из меты и разворачивают имя токена', () => {
    expect(statusColor('AUTO_CREATED', 'order', FALLBACK_META)).toBe('var(--state-attention)')
    expect(statusColor('DONE', 'order', FALLBACK_META)).toBe('var(--state-done)')
  })

  it('оставляют неизвестный статус без цвета — метка нейтральна', () => {
    expect(statusColor('WAT', 'order', FALLBACK_META)).toBeUndefined()
  })

  it('знают, какие статусы конечные', () => {
    expect(isTerminalStatus('DONE', 'order', FALLBACK_META)).toBe(true)
    expect(isTerminalStatus('REJECTED', 'order', FALLBACK_META)).toBe(true)
    expect(isTerminalStatus('IN_PROGRESS', 'order', FALLBACK_META)).toBe(false)
    expect(isTerminalStatus('WAT', 'order', FALLBACK_META)).toBe(false)
  })

  it('отдают список статусов нужной области для фильтра', () => {
    const codes = statusOptions('order', FALLBACK_META).map((s) => s.code)
    expect(codes).toContain('AUTO_CREATED')
    expect(codes).not.toContain('IN_REVIEW')
  })
})
