/**
 * Соответствие метрикам ТЗ: Precision против 0.7 и Recall против 0.5 по каждому
 * направлению.
 *
 * Виджет существует ровно для того, чтобы жюри увидело выполнение метрик, не
 * открывая ноутбук с моделью (spec §9). Поэтому целевые значения показаны
 * рядом с фактическими, а не спрятаны в подсказку.
 *
 * Цели приходят с бэкенда (`targetPrecision`, `targetRecall`) — фронт их не
 * хардкодит: поменяется ТЗ, поменяется и порог, без пересборки.
 *
 * Строка без чисел значит «точность не измерена». Пояснение приходит полем
 * `note`, виджет показывает его как есть: молчание о направлении читалось бы
 * как пропуск, а ноль — как провал.
 */
import { useMeta, useModelMetrics } from '@/shared/api/queries'
import type { ModelMetric } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/format'
import { directionLabel } from '@/shared/lib/risk'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

function ScoreCell({
  value,
  target,
  label,
}: {
  value: number | undefined
  target: number
  label: string
}) {
  if (value === undefined) {
    return (
      <span className="flex flex-col items-end leading-tight">
        <span className="text-[12px] text-text-dim" title={`${label} не измерена`}>
          не измерена
        </span>
        <span className="text-[11px] text-text-mute">цель {fmtScore(target)}</span>
      </span>
    )
  }
  const ok = value >= target
  return (
    <span className="flex flex-col items-end leading-tight">
      <span
        className="mono text-[13px]"
        style={{ color: ok ? 'var(--risk-low)' : 'var(--risk-high)' }}
        title={ok ? `${label} выше цели` : `${label} ниже цели ${fmtScore(target)}`}
      >
        {fmtScore(value)}
        {/* Знак дублирует цвет: только цветом кодировать нельзя. */}
        <span className="ml-1">{ok ? '✓' : '!'}</span>
      </span>
      <span className="text-[11px] text-text-mute">цель {fmtScore(target)}</span>
    </span>
  )
}

function MetricRow({ metric }: { metric: ModelMetric }) {
  const meta = useMeta()
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-line/60 py-1.5 last:border-b-0">
      <span className="min-w-0 truncate text-[13px] text-text-dim">
        {directionLabel(metric.direction, meta)}
      </span>
      <ScoreCell value={metric.precision} target={metric.targetPrecision} label="Precision" />
      <ScoreCell value={metric.recall} target={metric.targetRecall} label="Recall" />
      {metric.note ? (
        <span className="col-span-3 text-[11px] leading-snug text-text-mute">{metric.note}</span>
      ) : null}
    </li>
  )
}

export function ModelMetrics() {
  const query = useModelMetrics()

  return (
    <Panel title="Соответствие метрикам ТЗ" className="md:col-span-3 xl:col-span-4">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <>
          <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-b border-line pb-1 text-[11px] text-text-mute">
            <span>Направление</span>
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
