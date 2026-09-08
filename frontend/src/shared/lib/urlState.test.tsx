/**
 * Критерий приёмки (spec §12): ссылка на отфильтрованный журнал с открытой
 * карточкой восстанавливает то же состояние.
 *
 * Плюс инварианты записи в URL — от них зависит, работает ли кэш запросов.
 */
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useSearchParams } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { nextSort, parseSort, usePredictionFilters, useSelected, writeParams } from './urlState'

function wrapperFor(initial: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  }
}

describe('writeParams', () => {
  it('удаляет параметр вместо записи пустого значения', () => {
    const out = writeParams(new URLSearchParams('district=CAO'), { district: undefined })
    expect(out.toString()).toBe('')
  })

  it('сортирует повторяемые значения, чтобы ключ кэша был стабильным', () => {
    const a = writeParams(new URLSearchParams(), { level: ['HIGH', 'LOW'] }).toString()
    const b = writeParams(new URLSearchParams(), { level: ['LOW', 'HIGH'] }).toString()
    expect(a).toBe(b)
  })

  it('сбрасывает страницу при смене фильтра', () => {
    const out = writeParams(new URLSearchParams('page=7'), { level: ['HIGH'] })
    expect(out.get('page')).toBeNull()
  })

  it('не трогает страницу, если менялся не фильтр', () => {
    const out = writeParams(new URLSearchParams('page=7'), { prediction: 'P-1' })
    expect(out.get('page')).toBe('7')
  })

  it('не сбрасывает страницу, когда её меняют явно', () => {
    const out = writeParams(new URLSearchParams('level=HIGH'), { page: 3 })
    expect(out.get('page')).toBe('3')
  })
})

describe('сортировка', () => {
  it('разбирает строку сортировки', () => {
    expect(parseSort('probability:asc')).toEqual({ field: 'probability', dir: 'asc' })
    expect(parseSort(undefined)).toBeUndefined()
  })

  it('переворачивает направление на повторном клике по той же колонке', () => {
    expect(nextSort(undefined, 'probability')).toBe('probability:desc')
    expect(nextSort('probability:desc', 'probability')).toBe('probability:asc')
    expect(nextSort('probability:asc', 'probability')).toBe('probability:desc')
    expect(nextSort('probability:asc', 'horizon')).toBe('horizon:desc')
  })
})

describe('usePredictionFilters', () => {
  it('восстанавливает фильтры из ссылки', () => {
    const { result } = renderHook(() => usePredictionFilters(), {
      wrapper: wrapperFor('/journal?level=CRITICAL&level=HIGH&direction=ALPHA&district=CAO&page=2'),
    })
    expect(result.current.filters.level).toEqual(['CRITICAL', 'HIGH'])
    expect(result.current.filters.direction).toEqual(['ALPHA'])
    expect(result.current.filters.district).toBe('CAO')
    expect(result.current.filters.page).toBe(2)
    expect(result.current.isActive).toBe(true)
  })

  it('переключает значение мультивыбора туда и обратно', () => {
    const { result } = renderHook(() => usePredictionFilters(), {
      wrapper: wrapperFor('/journal'),
    })
    act(() => result.current.toggle('level', 'HIGH'))
    expect(result.current.filters.level).toEqual(['HIGH'])
    act(() => result.current.toggle('level', 'HIGH'))
    expect(result.current.filters.level).toEqual([])
  })

  it('сбрасывает страницу при смене фильтра', () => {
    const { result } = renderHook(() => usePredictionFilters(), {
      wrapper: wrapperFor('/journal?page=5'),
    })
    act(() => result.current.toggle('direction', 'ALPHA'))
    expect(result.current.filters.page).toBe(1)
  })

  it('чинит битый номер страницы вместо падения', () => {
    const { result } = renderHook(() => usePredictionFilters(), {
      wrapper: wrapperFor('/journal?page=абв'),
    })
    expect(result.current.filters.page).toBe(1)
  })
})

describe('useSelected', () => {
  it('читает выбранный прогноз из ссылки и умеет его снимать', () => {
    const { result } = renderHook(
      () => ({ selected: useSelected('prediction'), params: useSearchParams()[0] }),
      { wrapper: wrapperFor('/journal?level=HIGH&prediction=P-0042') },
    )
    expect(result.current.selected[0]).toBe('P-0042')

    act(() => result.current.selected[1](undefined))
    expect(result.current.selected[0]).toBeUndefined()
    // Фильтр при этом остаётся — закрытие карточки не сбрасывает выборку.
    expect(result.current.params.get('level')).toBe('HIGH')
  })
})
