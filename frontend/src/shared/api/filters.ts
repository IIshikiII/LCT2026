/**
 * Формы фильтров списков и их перевод в параметры запроса.
 *
 * Имена полей совпадают с именами параметров URL и параметров API — это
 * осознанно (docs/06-url-state.md): тогда перевод почти тождественный и лишнего
 * слоя маппинга не появляется.
 */
import type { QueryParams } from './client'

/** Серверная пагинация, spec §3: виртуализации нет, страница 50 строк. */
export const PAGE_SIZE = 50

export interface PredictionFilters {
  direction: string[]
  level: string[]
  status: string[]
  district?: string
  from?: string
  to?: string
  /** 'поле:asc' | 'поле:desc' */
  sort?: string
  page: number
}

export interface OrderFilters {
  status: string[]
  dueBefore?: string
  sort?: string
  page: number
}

export const emptyPredictionFilters: PredictionFilters = {
  direction: [],
  level: [],
  status: [],
  page: 1,
}

export const emptyOrderFilters: OrderFilters = {
  status: [],
  page: 1,
}

export function predictionParams(f: PredictionFilters): QueryParams {
  return {
    direction: f.direction,
    level: f.level,
    status: f.status,
    district: f.district,
    from: f.from,
    to: f.to,
    sort: f.sort,
    page: f.page,
    pageSize: PAGE_SIZE,
  }
}

export function orderParams(f: OrderFilters): QueryParams {
  return {
    status: f.status,
    dueBefore: f.dueBefore,
    sort: f.sort,
    page: f.page,
    pageSize: PAGE_SIZE,
  }
}

/** Карта берёт те же фильтры, но без пагинации и периода. */
export function facilityParams(f: PredictionFilters, bbox?: string): QueryParams {
  return {
    direction: f.direction,
    level: f.level,
    district: f.district,
    bbox,
  }
}

/** Есть ли хоть один активный фильтр — для подписи «сбросить» и EmptyState. */
export function hasActiveFilters(f: PredictionFilters): boolean {
  return (
    f.direction.length > 0 ||
    f.level.length > 0 ||
    f.status.length > 0 ||
    Boolean(f.district) ||
    Boolean(f.from) ||
    Boolean(f.to)
  )
}
