/**
 * Счётчики прогнозов по уровням риска.
 *
 * Уровни берутся из меты и сортируются по `order` — если бэкенд добавит пятый
 * уровень, он появится здесь сам. Числа набраны моноширинным с tabular-nums,
 * иначе при обновлении раз в минуту колонка «дёргается».
 *
 * Намеренно не «большая цифра + мелкая подпись»: это дежурный экран, а не
 * маркетинговая страница (docs/05-ui-kit.md).
 */
import { Link } from 'react-router-dom'
import { useDashboardSummary, useMeta } from '@/shared/api/queries'
import { fmtNumber } from '@/shared/lib/format'
import { levelColor, levelsBySeverity } from '@/shared/lib/risk'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

export function RiskCounters() {
  const meta = useMeta()
  const query = useDashboardSummary()

  return (
    <Panel title="Прогнозы по уровням риска" className="col-span-2">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <ul className="flex flex-col gap-1.5">
          {levelsBySeverity(meta).map((level) => {
            const count = query.data?.byLevel[level.code] ?? 0
            return (
              <li key={level.code}>
                <Link
                  to={`/journal?level=${encodeURIComponent(level.code)}`}
                  className="flex items-center gap-2 rounded px-1 py-1 hover:bg-raised"
                >
                  <span
                    aria-hidden="true"
                    className="inline-block h-4 w-[3px] shrink-0 rounded-[1px]"
                    style={{ background: levelColor(level.code, meta) }}
                  />
                  <span className="flex-1 text-[13px] text-text-dim">{level.label}</span>
                  <span className="mono text-[14px] text-text">{fmtNumber(count)}</span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </Panel>
  )
}
