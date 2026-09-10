/**
 * Заявки по статусам. Статусы — из меты (scope = 'order'), плюс те, что
 * реально пришли в счётчиках: неизвестный статус не должен исчезать из сводки.
 */
import { Link } from 'react-router-dom'
import { useDashboardSummary, useMeta } from '@/shared/api/queries'
import { fmtNumber } from '@/shared/lib/format'
import { statusColor, statusLabel, statusOptions } from '@/shared/lib/risk'
import { Panel } from '@/shared/ui/Panel'
import { ErrorState, Skeleton } from '@/shared/ui/states'

export function OrderCounters() {
  const meta = useMeta()
  const query = useDashboardSummary()
  const counts = query.data?.byOrderStatus ?? {}

  const known = statusOptions('order', meta).map((s) => s.code)
  const codes = [...known, ...Object.keys(counts).filter((code) => !known.includes(code))]

  return (
    <Panel title="Заявки по статусам" className="md:col-span-3 xl:col-span-2">
      {query.isPending ? (
        <Skeleton className="h-24" />
      ) : query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <ul className="flex flex-col gap-1">
          {codes.map((code) => (
            <li key={code}>
              <Link
                to={`/orders?orderStatus=${encodeURIComponent(code)}`}
                className="flex items-baseline justify-between gap-2 rounded px-1 py-0.5 hover:bg-raised"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ background: statusColor(code, 'order', meta) ?? 'var(--line-strong)' }}
                  />
                  <span className="min-w-0 truncate text-[13px] text-text-dim">
                    {statusLabel(code, 'order', meta)}
                  </span>
                </span>
                <span className="mono text-[13px] text-text">{fmtNumber(counts[code] ?? 0)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
