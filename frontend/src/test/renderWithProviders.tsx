/**
 * Рендер компонента со всем окружением приложения: клиент запросов и роутер.
 *
 * Роутер — MemoryRouter с заданным адресом, чтобы проверять восстановление
 * состояния из ссылки (docs/06-url-state.md). Обёртки `Routes` здесь нет
 * намеренно: ни один экран не читает параметры пути — всё состояние живёт в
 * параметрах запроса, поэтому компонент можно рендерить напрямую.
 *
 * Ретраи выключены: в тесте повторные попытки только удлиняют ожидание.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions, type RenderResult } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, refetchInterval: false, staleTime: 0 },
      mutations: { retry: false },
    },
  })
}

/** Пишет текущий адрес в DOM — так тест может проверить, что записалось в URL. */
function LocationProbe() {
  const location = useLocation()
  return (
    <div
      data-testid="location"
      data-search={location.search}
      data-pathname={location.pathname}
      hidden
    />
  )
}

export interface RenderWithProvidersOptions extends Omit<RenderOptions, 'wrapper'> {
  /** Начальный адрес, например '/journal?level=HIGH&prediction=P-0042'. */
  route?: string
  queryClient?: QueryClient
}

export function renderWithProviders(
  ui: ReactElement,
  options: RenderWithProvidersOptions = {},
): RenderResult & { queryClient: QueryClient } {
  const { route = '/', queryClient = createTestQueryClient(), ...rest } = options

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <LocationProbe />
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return { ...render(ui, { wrapper: Wrapper, ...rest }), queryClient }
}

/** Текущая строка запроса — для проверок «что попало в URL». */
export function currentSearch(): string {
  return document.querySelector('[data-testid="location"]')?.getAttribute('data-search') ?? ''
}

export function currentPath(): string {
  return document.querySelector('[data-testid="location"]')?.getAttribute('data-pathname') ?? ''
}
