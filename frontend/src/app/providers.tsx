/**
 * Провайдеры приложения: клиент запросов и роутер.
 *
 * Опрос раз в минуту задан здесь глобально (ADR 0005). Никакого другого
 * глобального состояния в приложении нет — фильтры и выбор живут в URL
 * (ADR 0001).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { BrowserRouter } from 'react-router-dom'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Горизонт прогноза — сутки, поэтому минуты задержки достаточно.
        refetchInterval: 60_000,
        refetchOnWindowFocus: true,
        staleTime: 30_000,
        // Одна повторная попытка: на дежурном экране лучше быстро показать
        // ошибку с кнопкой «повторить», чем молча ждать.
        retry: 1,
      },
      mutations: { retry: 0 },
    },
  })
}

/**
 * Единственный экземпляр клиента. Экспортируется, чтобы точка входа могла
 * сбросить кэш — этим пользуется дев-панель заглушек при смене флагов.
 */
export const queryClient = createQueryClient()

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  )
}
