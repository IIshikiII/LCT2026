/**
 * Состояние конвейера расчёта: время формирования прогноза против 5 минут,
 * минимальный горизонт против 24 часов, свежесть данных.
 *
 * Вторая половина доказательства метрик ТЗ — то, что не выражается через
 * Precision и Recall (spec §9).
 */
import { usePipelineHealth } from '@/shared/api/queries'
import { fmtDuration, fmtFreshness, fmtHours } from '@/shared/lib/format'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

function Row({
  label,
  value,
  target,
  ok,
}: {
  label: string
  value: string
  target: string
  ok: boolean
}) {
  return (
    <li className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1.5 last:border-b-0">
      <span className="min-w-0 truncate text-[13px] text-text-dim">{label}</span>
      <span className="flex flex-col items-end leading-tight">
        <span className="mono text-[13px]" style={{ color: ok ? 'var(--risk-low)' : 'var(--risk-high)' }}>
          {value}
          <span className="ml-1">{ok ? '✓' : '!'}</span>
        </span>
        <span className="text-[11px] text-text-mute">{target}</span>
      </span>
    </li>
  )
}

export function PipelineHealth() {
  const query = usePipelineHealth()
  const data = query.data

  return (
    <Panel title="Конвейер расчёта" className="col-span-2">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError || !data ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <ul className="flex flex-col">
          <Row
            label="Время формирования прогноза"
            value={fmtDuration(data.maxComputeMs)}
            target={`цель < ${fmtDuration(data.targetComputeMs)}`}
            ok={data.maxComputeMs < data.targetComputeMs}
          />
          <Row
            label="Минимальный горизонт"
            value={fmtHours(data.minHorizonHours)}
            target={`цель ≥ ${fmtHours(data.targetHorizonHours)}`}
            ok={data.minHorizonHours >= data.targetHorizonHours}
          />
          <Row
            label="Свежесть данных"
            value={fmtFreshness(data.freshnessMinutes)}
            target="опрос раз в минуту"
            ok={data.freshnessMinutes <= 60}
          />
        </ul>
      )}
    </Panel>
  )
}
