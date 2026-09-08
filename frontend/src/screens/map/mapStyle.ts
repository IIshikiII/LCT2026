/**
 * Стиль карты и выражения раскраски.
 *
 * По умолчанию карта не ходит в интернет (ADR 0008): подложка — фон цвета темы
 * плюс наши собственные линии коллекторов. Внешний стиль подключается
 * переменной VITE_MAP_STYLE_URL, если сеть на площадке надёжна.
 *
 * Раскраска точек строится из меты выражением `match` по коду уровня. Никакой
 * карты «код → цвет» в коде нет: пятый уровень риска раскрасится сам.
 */
import type { AppMeta } from '@/shared/api/types'
import { env } from '@/shared/config/env'
import { synthColor } from '@/shared/lib/risk'

/** MapLibre не понимает var(--x): достаём фактическое значение из темы. */
export function resolveCssColor(value: string, fallback: string): string {
  if (!value.startsWith('--')) return value || fallback
  if (typeof document === 'undefined') return fallback
  const resolved = getComputedStyle(document.documentElement).getPropertyValue(value).trim()
  return resolved || fallback
}

export const MAP_BACKGROUND = '#10151a'
export const MAP_LINE = '#2e3841'

/** Пустой тёмный стиль: подложки нет, всё рисуем сами. */
export function offlineStyle() {
  return {
    version: 8 as const,
    // Пустой список источников шрифтов — подписей на офлайн-стиле нет.
    sources: {},
    layers: [
      {
        id: 'background',
        type: 'background' as const,
        paint: { 'background-color': MAP_BACKGROUND },
      },
    ],
  }
}

export function mapStyle(): string | ReturnType<typeof offlineStyle> {
  return env.mapStyleUrl || offlineStyle()
}

/** Выражение цвета точки по коду уровня риска — собирается из меты. */
export function levelColorExpression(meta: AppMeta): unknown[] {
  const pairs: unknown[] = []
  for (const level of meta.riskLevels) {
    pairs.push(level.code, resolveCssColor(level.colorVar, synthColor(level.code)))
  }
  // Уровень, которого нет в мете, получает нейтральный серый — точка не пропадает.
  return ['match', ['get', 'level'], ...pairs, '#6b7883']
}

/** Радиус точки по вероятности: заметнее то, что вероятнее. */
export const radiusExpression: unknown[] = [
  'interpolate',
  ['linear'],
  ['get', 'probability'],
  0,
  3.5,
  1,
  8,
]

export const MOSCOW_CENTER: [number, number] = [37.6173, 55.7558]
