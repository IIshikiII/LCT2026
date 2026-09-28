/**
 * Хуки данных. Всё, что экраны знают о сети, — это функции отсюда.
 *
 * Опрос раз в минуту задан в providers.tsx глобально (ADR 0005). Здесь только
 * отклонения от него: /meta и трассы коллекторов не опрашиваются вовсе,
 * метрики моделей — реже.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { apiDelete, apiGet, apiPost } from './client'
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
  AlertListSchema,
  AppMetaSchema,
  DashboardSummarySchema,
  LoginChallengeSchema,
  FacilityCollectionSchema,
  LineCollectionSchema,
  ModelMetricListSchema,
  PipelineHealthSchema,
  PredictionDetailSchema,
  PredictionListSchema,
  PredictionPageSchema,
  SessionResponseSchema,
  TestStandSchema,
  TimeSeriesResponseSchema,
  WorkOrderPageSchema,
  WorkOrderSchema,
} from './schemas'
import type {
  Alert,
  AppMeta,
  DashboardSummary,
  FacilityCollection,
  LineCollection,
  LoginChallenge,
  ModelMetric,
  PageResult,
  PipelineHealth,
  Prediction,
  PredictionDetail,
  SessionResponse,
  TestStand,
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
    // Пока карта не сообщила границы, показываем прошлую выборку, а не пустоту.
    placeholderData: (prev) => prev,
  })
}

/**
 * Трассы коллекторов. Геометрия сети не меняется, поэтому запрашивается один
 * раз за сессию и не участвует в опросе раз в минуту (ADR 0005).
 */
export function useFacilityLines() {
  return useQuery({
    queryKey: queryKeys.facilityLines(),
    queryFn: ({ signal }) =>
      apiGet<LineCollection>(endpoints.facilityLines(), LineCollectionSchema, { signal }),
    staleTime: Infinity,
    gcTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: 1,
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

/**
 * Уведомления о тревоге. Опрос вдвое чаще журнала: тревога не ждёт минуту.
 * Работает и в фоне вкладки, чтобы число в заголовке окна было свежим.
 */
export const ALERTS_POLL_MS = 30_000

export function useAlerts() {
  return useQuery({
    queryKey: queryKeys.alerts(),
    queryFn: ({ signal }) => apiGet<Alert[]>(endpoints.alerts(), AlertListSchema, { signal }),
    refetchInterval: ALERTS_POLL_MS,
    refetchIntervalInBackground: true,
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

/* ---------------------------------------------------------------- сессия */

/**
 * Первый шаг входа: логин и пароль.
 *
 * Сессии здесь ещё нет. Ответ говорит, что делать дальше: прислать код или
 * сначала завести ключ. Разделение живёт на сервере, фронт только читает
 * поле `status`.
 */
export function useLogin() {
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      apiPost<LoginChallenge>(endpoints.login(), LoginChallengeSchema, body),
  })
}

/** Второй шаг входа. Он же подтверждает только что заведённый ключ. */
export function useConfirmCode() {
  return useMutation({
    mutationFn: (body: { mfaToken: string; code: string }) =>
      apiPost<SessionResponse>(endpoints.mfa(), SessionResponseSchema, body),
  })
}

/**
 * Выход. Токен снимает браузер, сервер записывает событие в журнал.
 *
 * Кэш запросов чистится целиком: в нём лежат данные прежней роли, и показать
 * их следующему вошедшему нельзя.
 */
export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<unknown>(endpoints.logout(), z.unknown(), {}),
    onSettled: () => qc.clear(),
  })
}

/* --------------------------------------------------------- тестовый стенд */

/**
 * Наборы учёток для жюри. Эндпоинт открыт и отвечает всегда, но при
 * выключенном флаге отдаёт `enabled: false`, и панель не рисуется.
 *
 * Опрос по времени здесь не нужен: список меняется только кнопками рядом.
 */
export function useTestStand() {
  return useQuery({
    queryKey: queryKeys.testStand(),
    queryFn: ({ signal }) =>
      apiGet<TestStand>(endpoints.testAccounts(), TestStandSchema, { signal }),
    refetchInterval: false,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    retry: false,
  })
}

/** Заводит следующий набор. Ответ — список целиком, его и кладём в кэш. */
export function useCreateTestSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<TestStand>(endpoints.testAccounts(), TestStandSchema, {}),
    onSuccess: (stand) => qc.setQueryData(queryKeys.testStand(), stand),
  })
}

export function useDeleteTestSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (set: number) =>
      apiDelete<TestStand>(endpoints.testAccountSet(set), TestStandSchema),
    onSuccess: (stand) => qc.setQueryData(queryKeys.testStand(), stand),
  })
}
