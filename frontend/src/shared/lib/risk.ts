/**
 * Работа с направлениями, уровнями риска и статусами — без единого `switch`.
 *
 * Это сердце главного архитектурного принципа (ADR 0002). Фронт получает коды
 * строками и ищет их описание в мете. Если описания нет — синтезирует его:
 * подписью становится сам код, цветом — детерминированный хеш кода.
 *
 * Именно поэтому пятое направление появляется в интерфейсе само, даже когда
 * /meta ещё не знает о нём, а данные уже пришли.
 */
import type { AppMeta, DirectionMeta, RiskLevelMeta, StatusMeta } from '@/shared/api/types'

/* --------------------------------------------------- детерминированный цвет */

/** Устойчивый хеш строки. Один код всегда даёт один цвет — между перезапусками. */
function hash(seed: string): number {
  let h = 2166136261
  for (let i = 0; i < seed.length; i += 1) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return Math.abs(h)
}

/**
 * Цвет для неизвестного кода. Насыщенность и светлота зафиксированы так, чтобы
 * цвет читался на тёмном фоне и не спорил со шкалой риска.
 */
export function synthColor(code: string): string {
  return `hsl(${hash(code) % 360} 38% 52%)`
}

/** Короткая подпись из кода: 'FLOOD_RISK' → 'FR'. */
function synthShortLabel(code: string): string {
  const initials = code
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((part) => part[0] ?? '')
    .join('')
  return (initials || code).slice(0, 3).toUpperCase()
}

/* ------------------------------------------------------------- направления */

/**
 * Описание направления. Никогда не возвращает undefined: неизвестный код
 * получает синтезированную мету, и экран не ломается.
 */
export function findDirection(code: string, meta: AppMeta): DirectionMeta {
  const known = meta.directions.find((d) => d.code === code)
  if (known) return known
  return {
    code,
    label: code,
    shortLabel: synthShortLabel(code),
    accent: synthColor(code),
    minHorizonHours: 24,
  }
}

export function directionLabel(code: string, meta: AppMeta): string {
  return findDirection(code, meta).label
}

export function directionAccent(code: string, meta: AppMeta): string {
  return findDirection(code, meta).accent
}

/**
 * Список направлений для фильтров.
 *
 * К направлениям из /meta добавляются коды, реально встреченные в данных.
 * Благодаря этому фильтр остаётся рабочим и на запасной мете, где
 * `directions: []` (см. shared/config/fallbacks.ts).
 */
export function directionOptions(meta: AppMeta, seenCodes: string[] = []): DirectionMeta[] {
  const result = [...meta.directions]
  const known = new Set(result.map((d) => d.code))
  for (const code of seenCodes) {
    if (code && !known.has(code)) {
      known.add(code)
      result.push(findDirection(code, meta))
    }
  }
  return result
}

/* ----------------------------------------------------------- уровни риска */

export function findRiskLevel(code: string, meta: AppMeta): RiskLevelMeta {
  const known = meta.riskLevels.find((l) => l.code === code)
  if (known) return known
  return { code, label: code, colorVar: synthColor(code), order: 0 }
}

export function levelLabel(code: string, meta: AppMeta): string {
  return findRiskLevel(code, meta).label
}

/**
 * Цвет уровня, пригодный для CSS.
 * `colorVar` вида '--risk-high' оборачивается в var(), любое другое значение
 * (готовый hex или hsl из синтеза) отдаётся как есть.
 */
export function levelColor(code: string, meta: AppMeta): string {
  const colorVar = findRiskLevel(code, meta).colorVar
  return colorVar.startsWith('--') ? `var(${colorVar})` : colorVar
}

/** Уровни по возрастанию тяжести — порядок задаёт поле order, а не массив. */
export function sortedLevels(meta: AppMeta): RiskLevelMeta[] {
  return [...meta.riskLevels].sort((a, b) => a.order - b.order)
}

/** Уровни по убыванию тяжести — для счётчиков и легенды карты. */
export function levelsBySeverity(meta: AppMeta): RiskLevelMeta[] {
  return sortedLevels(meta).reverse()
}

export function levelOrder(code: string, meta: AppMeta): number {
  return findRiskLevel(code, meta).order
}

/* --------------------------------------------------------------- статусы */

export function findStatus(code: string, scope: string, meta: AppMeta): StatusMeta {
  const known = meta.statuses.find((s) => s.code === code && s.scope === scope)
  if (known) return known
  const anyScope = meta.statuses.find((s) => s.code === code)
  return anyScope ?? { code, label: code, scope }
}

export function statusLabel(code: string, scope: string, meta: AppMeta): string {
  return findStatus(code, scope, meta).label
}

export function statusOptions(scope: string, meta: AppMeta): StatusMeta[] {
  return meta.statuses.filter((s) => s.scope === scope)
}

/* --------------------------------------------------------------- прочее */

export function districtLabel(code: string | undefined, meta: AppMeta): string {
  if (!code) return ''
  return meta.districts.find((d) => d.code === code)?.label ?? code
}
