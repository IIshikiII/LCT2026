/**
 * Разбивка прогнозов по направлениям.
 *
 * Ключи приходят из `/dashboard/summary` и раскрываются через мету. Появилось
 * направление — появилась строка; ни списка направлений, ни их цветов в этом
 * файле нет (ADR 0002).
 */
import { Link } from 'react-router-dom'
import { useDashboardSummary, useMeta } from '@/shared/api/queries'
import { fmtNumber } from '@/shared/lib/format'
import { directionOptions } from '@/shared/lib/risk'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

export function DirectionSplit() {
  const meta = useMeta()
  const query = useDashboardSummary()
  const counts = query.data?.byDirection ?? {}

  // Направления из меты плюс те, что реально встретились в счётчиках.
  const directions = directionOptions(meta, Object.keys(counts))
  const max = Math.max(...directions.map((d) => counts[d.code] ?? 0), 1)

  return (
    <Panel title="Прогнозы по направлениям" className="col-span-3">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <ul className="flex flex-col gap-2">
          {directions.map((direction) => {
            const count = counts[direction.code] ?? 0
            return (
              <li key={direction.code}>
                <Link
                  to={`/journal?direction=${encodeURIComponent(direction.code)}`}
                  className="flex flex-col gap-1 rounded px-1 py-0.5 hover:bg-raised"
                >
                  <span className="flex items-baseline justify-between gap-2 text-[13px]">
                    <span className="min-w-0 truncate text-text-dim">{direction.label}</span>
                    <span className="mono shrink-0 text-text">{fmtNumber(count)}</span>
                  </span>
                  <span className="h-1.5 w-full rounded-[1px] bg-sunken">
                    <span
                      aria-hidden="true"
                      className="block h-full rounded-[1px]"
                      style={{ width: `${(count / max) * 100}%`, background: direction.accent }}
                    />
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </Panel>
  )
}
