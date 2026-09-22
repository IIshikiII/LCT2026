/**
 * Состояние интерфейса живёт в параметрах URL. Глобального стора нет (ADR 0001).
 *
 * Инварианты, за которыми надо следить при правках (docs/06-url-state.md):
 *  1. смена любого фильтра сбрасывает page на 1;
 *  2. пустое значение удаляется из URL, а не пишется как ?district=;
 *  3. повторяемые значения сортируются — иначе один и тот же набор фильтров
 *     даёт два разных ключа кэша и два одинаковых запроса.
 *
 * Пункты 2 и 3 вынесены в чистую функцию `writeParams`, чтобы их можно было
 * проверить тестом без рендеринга.
 *
 * Объекты фильтров пересоздаются на каждый рендер, и это нормально: TanStack
 * Query хеширует ключ структурно, а не по ссылке. Мемоизировать нечего.
 */
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { OrderFilters, PredictionFilters } from '@/shared/api/filters'

export type ParamPatch = Record<string, string | string[] | number | undefined | null>

/** Ключи, которые считаются фильтрами: их изменение сбрасывает страницу. */
const FILTER_KEYS = [
  'direction',
  'level',
  'status',
  'orderStatus',
  'district',
  'assignee',
  'from',
  'to',
  'dueBefore',
]

/** Чистая запись патча в параметры. Реализует инварианты 1–3. */
export function writeParams(current: URLSearchParams, patch: ParamPatch): URLSearchParams {
  const next = new URLSearchParams(current)
  let touchedFilter = false

  for (const [key, value] of Object.entries(patch)) {
    if (FILTER_KEYS.includes(key)) touchedFilter = true
    next.delete(key)

    if (value === undefined || value === null || value === '') continue

    if (Array.isArray(value)) {
      for (const item of [...value].filter(Boolean).sort()) next.append(key, item)
    } else {
      next.set(key, String(value))
    }
  }

  // Инвариант 1: сменился фильтр — снова первая страница.
  if (touchedFilter && !('page' in patch)) next.delete('page')

  return next
}

function readPage(raw: string | null): number {
  const page = Number(raw ?? '1')
  return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1
}

/* -------------------------------------------------------- низкий уровень */

export function useParam(name: string): string | undefined {
  const [params] = useSearchParams()
  return params.get(name) ?? undefined
}

export function useParamList(name: string): string[] {
  const [params] = useSearchParams()
  return [...params.getAll(name)].sort()
}

/** Записать патч в URL. Функция стабильна по ссылке. */
export function useWriteParams() {
  const [, setParams] = useSearchParams()
  return useCallback(
    (patch: ParamPatch, options?: { replace?: boolean }) => {
      setParams((prev) => writeParams(prev, patch), { replace: options?.replace ?? false })
    },
    [setParams],
  )
}

/* ------------------------------------------------------------- сортировка */

export interface SortState {
  field: string
  dir: 'asc' | 'desc'
}

export function parseSort(value: string | undefined): SortState | undefined {
  if (!value) return undefined
  const [field, dir] = value.split(':')
  if (!field) return undefined
  return { field, dir: dir === 'asc' ? 'asc' : 'desc' }
}

/** Клик по заголовку: та же колонка — переворот, другая — сразу по убыванию. */
export function nextSort(current: string | undefined, field: string): string {
  const parsed = parseSort(current)
  return parsed?.field === field && parsed.dir === 'desc' ? `${field}:asc` : `${field}:desc`
}

/* ------------------------------------------------------- фильтры прогнозов */

export type PredictionListKey = 'direction' | 'level' | 'status'

export interface PredictionFilterApi {
  filters: PredictionFilters
  /** Одиночное значение: район, период. */
  setValue: (key: string, value: string | number | undefined) => void
  /** Мультивыбор: добавить или убрать код. */
  toggle: (key: PredictionListKey, code: string) => void
  setPage: (page: number) => void
  toggleSort: (field: string) => void
  reset: () => void
  isActive: boolean
}

export function usePredictionFilters(): PredictionFilterApi {
  const [params] = useSearchParams()
  const write = useWriteParams()

  const filters: PredictionFilters = {
    direction: [...params.getAll('direction')].sort(),
    level: [...params.getAll('level')].sort(),
    status: [...params.getAll('status')].sort(),
    district: params.get('district') ?? undefined,
    assignee: params.get('assignee') ?? undefined,
    from: params.get('from') ?? undefined,
    to: params.get('to') ?? undefined,
    sort: params.get('sort') ?? undefined,
    page: readPage(params.get('page')),
  }

  return {
    filters,
    setValue: (key, value) => write({ [key]: value }),
    toggle: (key, code) => {
      const current = filters[key]
      write({ [key]: current.includes(code) ? current.filter((c) => c !== code) : [...current, code] })
    },
    setPage: (page) => write({ page: page > 1 ? page : undefined }),
    toggleSort: (field) => write({ sort: nextSort(filters.sort, field), page: undefined }),
    reset: () =>
      write({
        direction: undefined,
        level: undefined,
        status: undefined,
        district: undefined,
        assignee: undefined,
        from: undefined,
        to: undefined,
        page: undefined,
      }),
    isActive:
      filters.direction.length > 0 ||
      filters.level.length > 0 ||
      filters.status.length > 0 ||
      Boolean(filters.district ?? filters.assignee ?? filters.from ?? filters.to),
  }
}

/* --------------------------------------------------------- фильтры заявок */

export interface OrderFilterApi {
  filters: OrderFilters
  toggleStatus: (code: string) => void
  setValue: (key: string, value: string | number | undefined) => void
  setPage: (page: number) => void
  toggleSort: (field: string) => void
  reset: () => void
  isActive: boolean
}

export function useOrderFilters(): OrderFilterApi {
  const [params] = useSearchParams()
  const write = useWriteParams()

  const filters: OrderFilters = {
    status: [...params.getAll('orderStatus')].sort(),
    dueBefore: params.get('dueBefore') ?? undefined,
    sort: params.get('sort') ?? undefined,
    page: readPage(params.get('page')),
  }

  return {
    filters,
    toggleStatus: (code) =>
      write({
        orderStatus: filters.status.includes(code)
          ? filters.status.filter((c) => c !== code)
          : [...filters.status, code],
      }),
    setValue: (key, value) => write({ [key]: value }),
    setPage: (page) => write({ page: page > 1 ? page : undefined }),
    toggleSort: (field) => write({ sort: nextSort(filters.sort, field), page: undefined }),
    reset: () => write({ orderStatus: undefined, dueBefore: undefined, page: undefined }),
    isActive: filters.status.length > 0 || Boolean(filters.dueBefore),
  }
}

/* ------------------------------------------------------ выбранная сущность */

/**
 * Выбор сущности для правой панели. Обычная запись в историю: «назад»
 * закрывает карточку — так и задумано.
 */
export function useSelected(name: 'prediction' | 'order') {
  const [params] = useSearchParams()
  const write = useWriteParams()
  const id = params.get(name) ?? undefined
  const select = useCallback(
    (value: string | undefined) => write({ [name]: value }),
    [write, name],
  )
  return [id, select] as const
}
