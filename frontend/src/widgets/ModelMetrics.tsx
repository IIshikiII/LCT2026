/**
 * Здоровье модели: Precision и Recall модели рядом с наивным правилом на той
 * же выборке.
 *
 * Умолчания ТЗ 0.7 и 0.5 порогом приёмки не являются (ТЗ §9, ответ заказчика
 * 2.3), поэтому виджет их не показывает. Модель сравнивается с правилом
 * «событие было вчера»: это честная планка для редкого события. Название
 * правила приходит с сервера полем `baselineRule`, фронт его не знает.
 *
 * Строка без чисел значит «точность не измерена». Пояснение приходит полем
 * `note`, виджет показывает его как есть: молчание о направлении читалось бы
 * как пропуск, а ноль — как провал.
 */
import { useMeta, useModelMetrics } from '@/shared/api/queries'
import type { ModelMetric } from '@/shared/api/types'
import { cn } from '@/shared/lib/cn'
import { fmtScore } from '@/shared/lib/format'
import { directionLabel } from '@/shared/lib/risk'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

const GRID = 'grid grid-cols-[minmax(0,1fr)_4rem_4rem_4rem_4rem] items-baseline gap-x-2'

function Score({ value, dim = false }: { value: number | undefined; dim?: boolean }) {
  return (
    <span className={cn('mono text-right text-[13px]', dim ? 'text-text-dim' : 'text-text')}>
      {value === undefined ? '—' : fmtScore(value)}
    </span>
  )
}

function MetricRow({ metric }: { metric: ModelMetric }) {
  const meta = useMeta()
  const label = directionLabel(metric.direction, meta)
  const measured = metric.precision !== undefined
  return (
    <li className={cn(GRID, 'border-b border-line/60 py-1.5 last:border-b-0')}>
      <span className="min-w-0">
        <span className="block truncate text-[13px] text-text-dim">{label}</span>
        {metric.baselineRule ? (
          <span className="block truncate text-[11px] text-text-mute" title={metric.baselineRule}>
            правило: {metric.baselineRule}
          </span>
        ) : null}
      </span>
      {measured ? (
        <>
          <Score value={metric.precision} />
          <Score value={metric.recall} />
          <Score value={metric.baselinePrecision} dim />
          <Score value={metric.baselineRecall} dim />
        </>
      ) : (
        <span className="col-span-4 text-right text-[12px] text-text-dim">не измерена</span>
      )}
      {metric.note ? (
        <span className="col-span-5 text-[11px] leading-snug text-text-mute">{metric.note}</span>
      ) : null}
    </li>
  )
}

export function ModelMetrics() {
  const query = useModelMetrics()

  return (
    <Panel title="Здоровье модели" className="md:col-span-3 xl:col-span-4">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <>
          <div className={cn(GRID, 'text-[11px] text-text-mute')}>
            <span />
            <span className="col-span-2 text-center">Модель</span>
            <span className="col-span-2 text-center">Правило</span>
          </div>
          <div className={cn(GRID, 'border-b border-line pb-1 text-[11px] text-text-mute')}>
            <span>Направление</span>
            <span className="text-right" title="Precision, точность">
              Precision
            </span>
            <span className="text-right" title="Recall, полнота">
              Recall
            </span>
            <span className="text-right">Precision</span>
            <span className="text-right">Recall</span>
          </div>
          <ul className="flex flex-col">
            {(query.data ?? []).map((metric) => (
              <MetricRow key={metric.direction} metric={metric} />
            ))}
          </ul>
        </>
      )}
    </Panel>
  )
}
