/**
 * Конструкторы блоков карточки. Общие для всех направлений — различается только
 * содержимое, которое кладёт конкретный плагин.
 *
 * Формы `data` здесь и разбор в src/card/blocks/ должны совпадать. Если меняешь
 * форму — правь обе стороны в одном коммите; при расхождении блок деградирует
 * до GenericBlock, экран не упадёт, но выглядеть будет плохо.
 */
import type { CardBlock, Series, SeriesPoint } from '@/shared/api/types'
import { hoursFrom, iso, type Rng } from '../db/rng'

export interface FactorItem {
  label: string
  /** вклад в итоговую вероятность, -1..1 */
  weight: number
  /** фактическое значение признака человеческим языком */
  value?: string
}

/** Вклад признаков — горизонтальные полосы в обе стороны от нуля. */
export function factorsBlock(title: string, items: FactorItem[]): CardBlock {
  const sorted = [...items].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight))
  return { type: 'factors', title, data: { items: sorted } }
}

/** График показаний с вертикальной отметкой момента прогноза. */
export function timeseriesBlock(title: string, series: Series[], markerAt: string): CardBlock {
  return { type: 'timeseries', title, data: { series, markerAt } }
}

export interface TimelineEvent {
  at: string
  title: string
  /** 'alarm' | 'repair' | 'inspection' | 'access' | 'work' — свободная строка */
  kind: string
  note?: string
}

/** События объекта на оси времени. */
export function timelineBlock(title: string, events: TimelineEvent[]): CardBlock {
  const sorted = [...events].sort((a, b) => (a.at < b.at ? 1 : -1))
  return { type: 'timeline', title, data: { events: sorted } }
}

export interface TableColumn {
  key: string
  header: string
  align?: 'left' | 'right'
}

/** Произвольная таблица. */
export function tableBlock(
  title: string,
  columns: TableColumn[],
  rows: Record<string, unknown>[],
): CardBlock {
  return { type: 'table', title, data: { columns, rows } }
}

/** Список пар «ключ — значение»: паспорт объекта, сводка. */
export function keyValueBlock(title: string, items: { label: string; value: unknown }[]): CardBlock {
  return { type: 'keyvalue', title, data: { items } }
}

/**
 * Блок намеренно неизвестного типа.
 *
 * Добавляется небольшой доле прогнозов, чтобы на демо было видно: незнакомый
 * тип рисуется GenericBlock, а не роняет карточку (критерий приёмки spec §12).
 */
export function experimentalBlock(rnd: Rng): CardBlock {
  return {
    type: 'unknown-experimental',
    title: 'Экспериментальный признак модели',
    data: {
      модельВерсия: `v${rnd.int(2, 4)}.${rnd.int(0, 9)}`,
      вкладПризнака: rnd.float(0.01, 0.24, 3),
      источник: 'экспериментальная ветка конвейера',
      комментарий: 'Тип блока фронтенду неизвестен — он нарисован запасным рендерером.',
    },
  }
}

export interface SeriesShape {
  name: string
  unit?: string
  /** сколько часов истории */
  hours: number
  base: number
  /** амплитуда суточной волны */
  daily: number
  /** линейный тренд за весь период */
  trend: number
  noise: number
  /** не опускать значения ниже */
  floor?: number
}

/**
 * Ряд с суточной волной, трендом и шумом.
 * Суточность обязательна: без неё графики выглядят как случайные числа, и
 * жюри справедливо спрашивает, что здесь вообще предсказано.
 */
export function buildSeries(rnd: Rng, end: Date, shape: SeriesShape): Series {
  const points: SeriesPoint[] = []
  const stepHours = shape.hours > 96 ? 3 : 1
  const count = Math.floor(shape.hours / stepHours)

  for (let i = count; i >= 0; i -= 1) {
    const at = hoursFrom(end, -i * stepHours)
    const progress = 1 - i / count
    const hourOfDay = at.getUTCHours()
    const daily = Math.sin(((hourOfDay - 4) / 24) * Math.PI * 2) * shape.daily
    const value = shape.base + daily + shape.trend * progress + rnd.gauss(0, shape.noise)
    points.push({
      t: iso(at),
      v: Number(Math.max(shape.floor ?? -Infinity, value).toFixed(2)),
    })
  }

  return { name: shape.name, unit: shape.unit, points }
}

/** Разреженный ряд событий: столбики «сколько срабатываний в сутки». */
export function buildDailyCounts(rnd: Rng, end: Date, days: number, base: number, growth: number): Series {
  const points: SeriesPoint[] = []
  for (let i = days; i >= 0; i -= 1) {
    const at = hoursFrom(end, -i * 24)
    const progress = 1 - i / days
    const value = Math.max(0, Math.round(base + growth * progress + rnd.gauss(0, 1.1)))
    points.push({ t: iso(at), v: value })
  }
  return { name: 'Срабатываний за сутки', unit: 'шт', points }
}
