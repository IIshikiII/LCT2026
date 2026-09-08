/**
 * Детерминированная случайность.
 *
 * Сид фиксирован: данные одинаковы между перезапусками. Демо на защите
 * воспроизводимо, скриншоты не «плывут», тесты не мигают (docs/04-mocks.md).
 *
 * Два уровня:
 *  - `faker` с общим сидом — для имён, адресов и прочей фактуры;
 *  - `makeRng(seed)` — маленький ГПСЧ, привязанный к конкретной сущности.
 *    Нужен, чтобы содержимое карточки прогноза P-0042 не зависело от того,
 *    сколько сущностей сгенерировалось до него.
 */
import { faker } from '@faker-js/faker'

export const SEED = 20260908

/** Единый момент «сейчас» для всего сида. Иначе тесты про горизонт мигали бы. */
export const NOW = new Date('2026-09-08T09:00:00.000Z')

export function resetFaker(): void {
  faker.seed(SEED)
  faker.setDefaultRefDate(NOW)
}

export { faker }

export interface Rng {
  /** [0, 1) */
  next(): number
  int(min: number, max: number): number
  float(min: number, max: number, digits?: number): number
  bool(probability?: number): boolean
  pick<T>(items: readonly T[]): T
  sample<T>(items: readonly T[], count: number): T[]
  /** Нормальное распределение — для правдоподобной телеметрии. */
  gauss(mean: number, sigma: number): number
}

function hashSeed(seed: string | number): number {
  const text = String(seed)
  let h = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** mulberry32 — короткий, быстрый, повторяемый. */
export function makeRng(seed: string | number): Rng {
  let state = hashSeed(seed)

  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  const int = (min: number, max: number) => Math.floor(next() * (max - min + 1)) + min

  return {
    next,
    int,
    float: (min, max, digits = 2) => Number((next() * (max - min) + min).toFixed(digits)),
    bool: (probability = 0.5) => next() < probability,
    pick: <T,>(items: readonly T[]): T => {
      const item = items[int(0, items.length - 1)]
      if (item === undefined) throw new Error('pick: пустой список')
      return item
    },
    sample: <T,>(items: readonly T[], count: number): T[] => {
      const copy = [...items]
      for (let i = copy.length - 1; i > 0; i -= 1) {
        const j = int(0, i)
        const a = copy[i] as T
        const b = copy[j] as T
        copy[i] = b
        copy[j] = a
      }
      return copy.slice(0, Math.min(count, copy.length))
    },
    gauss: (mean, sigma) => {
      const u = Math.max(next(), Number.EPSILON)
      const v = next()
      return mean + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
    },
  }
}

/** Сдвиг от NOW в часах. Все относительные даты в сиде считаются от него. */
export function hoursFrom(base: Date, hours: number): Date {
  return new Date(base.getTime() + hours * 3600_000)
}

export function iso(date: Date): string {
  return date.toISOString()
}
