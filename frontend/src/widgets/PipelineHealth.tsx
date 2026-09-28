/**
 * Состояние конвейера расчёта: время формирования прогноза, горизонт прогноза,
 * задержка потока, свежесть данных.
 *
 * Требования ТЗ (5 минут, 24 часа) на виджете не подписаны: он показывает
 * замер. Значение, которое требование нарушает, выделено цветом и знаком «!»,
 * а подсказка называет требование. Границы приходят с сервера (spec §9).
 */
import { usePipelineHealth } from '@/shared/api/queries'
import { fmtDuration, fmtFreshness, fmtHours } from '@/shared/lib/format'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

function Row({
  label,
  value,
  broken = false,
  requirement,
}: {
  label: string
  value: string
  /** значение нарушает требование ТЗ */
  broken?: boolean
  /** текст требования для подсказки у нарушенного значения */
  requirement?: string
}) {
  return (
    <li className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1.5 last:border-b-0">
      <span className="min-w-0 truncate text-[13px] text-text-dim">{label}</span>
      <span
        className="mono text-[13px]"
        style={broken ? { color: 'var(--color-risk-high)' } : undefined}
        title={broken ? requirement : undefined}
      >
        {value}
        {/* Знак дублирует цвет: только цветом кодировать нельзя. */}
        {broken ? <span className="ml-1">!</span> : null}
      </span>
    </li>
  )
}

export function PipelineHealth() {
  const query = usePipelineHealth()
  const data = query.data

  return (
    <Panel title="Конвейер расчёта" className="md:col-span-3 xl:col-span-2">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError || !data ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <ul className="flex flex-col">
          <Row
            label="Время формирования прогноза"
            value={fmtDuration(data.maxComputeMs)}
            broken={data.maxComputeMs >= data.targetComputeMs}
            requirement={`ТЗ: меньше ${fmtDuration(data.targetComputeMs)}`}
          />
          <Row
            label="Горизонт прогноза"
            value={fmtHours(data.minHorizonHours)}
            broken={data.minHorizonHours < data.targetHorizonHours}
            requirement={`ТЗ: не меньше ${fmtHours(data.targetHorizonHours)}`}
          />
          {data.targetStreamLagMs !== undefined && (
            <Row
              label="Задержка потока"
              value={data.streamLagMs == null ? 'событий не было' : fmtDuration(data.streamLagMs)}
              broken={data.streamLagMs != null && data.streamLagMs >= data.targetStreamLagMs}
              requirement={`ТЗ: меньше ${fmtDuration(data.targetStreamLagMs)}`}
            />
          )}
          <Row
            label="Свежесть данных"
            value={fmtFreshness(data.freshnessMinutes)}
            broken={data.freshnessMinutes > 60}
            requirement="данные старше часа"
          />
        </ul>
      )}
    </Panel>
  )
}
