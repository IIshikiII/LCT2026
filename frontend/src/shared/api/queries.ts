/**
 * Хуки данных. Всё, что экраны знают о сети, — это функции отсюда.
 *
 * Опрос раз в минуту задан в providers.tsx глобально (ADR 0005). Здесь только
 * отклонения от него: /meta не опрашивается вовсе, метрики — реже.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { apiGet, apiPost } from './client'
import { endpoints } from './endpoints'
import {
  facilityParams,
  orderParams,
  predictionParams,
  type OrderFilters,
  type PredictionFilters,
} from './filters'
import { queryKeys } from './queryKeys'
import {
  AppMetaSchema,
  DashboardSummarySchema,
  FacilityCollectionSchema,
  ModelMetricListSchema,
  PipelineHealthSchema,
  PredictionDetailSchema,
  PredictionListSchema,
  PredictionPageSchema,
  TimeSeriesResponseSchema,
  WorkOrderPageSchema,
  WorkOrderSchema,
} from './schemas'
import type {
  AppMeta,
  DashboardSummary,
  FacilityCollection,
  ModelMetric,
  PageResult,
  PipelineHealth,
  Prediction,
  PredictionDetail,
  TimeSeriesResponse,
  WorkOrder,
} from './types'

/* ------------------------------------------------------------------ мета */

export function useMetaQuery() {
  return useQuery({
    queryKey: queryKeys.meta(),
    queryFn: () => apiGet<AppMeta>(endpoints.meta(), AppMetaSchema),
    // Описание предметной области не меняется в течение сессии.
    staleTime: Infinity,
    gcTime: Infinity,
    refetchInterval: false,
    retry: 1,
    // Ниже — защита от цикла перезапросов при недоступной /meta.
    //
    // Каркас приложения показывает скелет, пока запрос в состоянии pending.
    // Как только запрос падает, разворачиваются экраны — а каждый экран
    // подписывается на ту же мету новым наблюдателем. С поведением по
    // умолчанию новый наблюдатель на упавшем запросе запускает повторную
    // попытку, запрос снова становится «в работе», экраны сворачиваются,
    // наблюдатели отписываются — и всё повторяется по кругу, обрывая на лету
    // запросы списков.
    //
    // Для /meta это ещё и правильное поведение по существу: она грузится один
    // раз при старте, и если её нет — мы работаем на запасных конфигах, а не
    // долбим бэкенд.
    retryOnMount: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}

/**
 * Мета для рендеринга. Никогда не бросает и никогда не возвращает undefined:
 * пока грузится или если /meta сломана — отдаёт запасную (spec §12).
 * Поэтому компонентам не нужны проверки на null.
 */
export function useMeta(): AppMeta {
  return useMetaQuery().data ?? FALLBACK_META
}

/** true, когда интерфейс построен на запасных конфигах — показываем в шапке. */
export function useMetaIsFallback(): boolean {
  const q = useMetaQuery()
  return q.isError || (!q.isPending && !q.data)
}

/* -------------------------------------------------------------- прогнозы */

export function usePredictions(filters: PredictionFilters) {
  return useQuery({
    queryKey: queryKeys.predictions(filters),
    queryFn: ({ signal }) =>
      apiGet<PageResult<Prediction>>(endpoints.predictions(), PredictionPageSchema, {
        params: predictionParams(filters),
        signal,
      }),
    placeholderData: (prev) => prev, // не мигать пустотой при смене страницы
  })
}

export function usePrediction(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.prediction(id ?? ''),
    queryFn: ({ signal }) =>
      apiGet<PredictionDetail>(endpoints.prediction(id as string), PredictionDetailSchema, {
        signal,
      }),
    enabled: Boolean(id),
  })
}

export function usePredictionTimeseries(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.predictionTimeseries(id ?? ''),
    queryFn: ({ signal }) =>
      apiGet<TimeSeriesResponse>(
        endpoints.predictionTimeseries(id as string),
        TimeSeriesResponseSchema,
        { signal },
      ),
    enabled: Boolean(id) && enabled,
  })
}

/**
 * Единый эндпоинт действий над прогнозом (ADR 0004).
 * Ответ — сущность целиком, поэтому кладём её в кэш и не гадаем, что изменилось.
 */
export function usePredictionAction(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ code, values }: { code: string; values: Record<string, unknown> }) =>
      apiPost<PredictionDetail>(endpoints.predictionAction(id, code), PredictionDetailSchema, values),
    onSuccess: (detail) => {
      qc.setQueryData(queryKeys.prediction(id), detail)
      void qc.invalidateQueries({ queryKey: ['predictions'] })
      void qc.invalidateQueries({ queryKey: ['orders'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

/* ----------------------------------------------------------------- карта */

export function useFacilities(filters: PredictionFilters, bbox?: string) {
  return useQuery({
    queryKey: [...queryKeys.facilities(filters), bbox ?? ''],
    queryFn: ({ signal }) =>
      apiGet<FacilityCollection>(endpoints.facilities(), FacilityCollectionSchema, {
        params: facilityParams(filters, bbox),
        signal,
      }),
  })
}

/* ---------------------------------------------------------------- заявки */

export function useOrders(filters: OrderFilters) {
  return useQuery({
    queryKey: queryKeys.orders(filters),
    queryFn: ({ signal }) =>
      apiGet<PageResult<WorkOrder>>(endpoints.orders(), WorkOrderPageSchema, {
        params: orderParams(filters),
        signal,
      }),
    placeholderData: (prev) => prev,
  })
}

export function useOrder(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.order(id ?? ''),
    queryFn: ({ signal }) =>
      apiGet<WorkOrder>(endpoints.order(id as string), WorkOrderSchema, { signal }),
    enabled: Boolean(id),
  })
}

export function useOrderAction(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ code, values }: { code: string; values: Record<string, unknown> }) =>
      apiPost<WorkOrder>(endpoints.orderAction(id, code), WorkOrderSchema, values),
    onSuccess: (order) => {
      qc.setQueryData(queryKeys.order(id), order)
      void qc.invalidateQueries({ queryKey: ['orders'] })
      void qc.invalidateQueries({ queryKey: ['predictions'] })
      void qc.invalidateQueries({ queryKey: ['prediction'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
      void qc.invalidateQueries({ queryKey: ['metrics'] })
    },
  })
}

/* -------------------------------------------------- метрики и дашборд */

export function useModelMetrics() {
  return useQuery({
    queryKey: queryKeys.modelMetrics(),
    queryFn: ({ signal }) =>
      apiGet<ModelMetric[]>(endpoints.modelMetrics(), ModelMetricListSchema, { signal }),
    // Метрики пересчитываются реже, чем идёт опрос списков.
    staleTime: 5 * 60_000,
  })
}

export function usePipelineHealth() {
  return useQuery({
    queryKey: queryKeys.pipelineHealth(),
    queryFn: ({ signal }) =>
      apiGet<PipelineHealth>(endpoints.pipelineHealth(), PipelineHealthSchema, { signal }),
  })
}

export function useDashboardSummary() {
  return useQuery({
    queryKey: queryKeys.dashboardSummary(),
    queryFn: ({ signal }) =>
      apiGet<DashboardSummary>(endpoints.dashboardSummary(), DashboardSummarySchema, { signal }),
  })
}

export function useTopRisks(limit = 10) {
  return useQuery({
    queryKey: queryKeys.topRisks(limit),
    queryFn: ({ signal }) =>
      apiGet<Prediction[]>(endpoints.dashboardTopRisks(), PredictionListSchema, {
        params: { limit },
        signal,
      }),
  })
}
