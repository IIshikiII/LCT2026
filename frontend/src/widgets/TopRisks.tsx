/**
 * Топ-10 объектов риска. Клик открывает карточку в журнале — дашборд правой
 * панели не имеет, а дублировать её здесь незачем (spec §1).
 */
import { useNavigate } from 'react-router-dom'
import { useMeta, useTopRisks } from '@/shared/api/queries'
import { DataTable } from '@/table/DataTable'
import { predictionColumns, resolveColumns } from '@/table/columnRegistry'
import { Panel } from '@/shared/ui/Panel'
import { EmptyState, ErrorState, TableSkeleton } from '@/shared/ui/states'

/** Компактный набор колонок: в виджете нет места на все девять. */
const COLUMNS = ['risk', 'direction', 'facility', 'probability', 'horizon']

export function TopRisks() {
  const meta = useMeta()
  const navigate = useNavigate()
  const query = useTopRisks(10)

  return (
    <Panel title="Топ-10 объектов риска" className="col-span-3 row-span-2" padded={false}>
      {query.isPending ? (
        <TableSkeleton rows={10} />
      ) : query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <DataTable
          caption="Десять объектов с наибольшей вероятностью события"
          columns={resolveColumns(COLUMNS, predictionColumns)}
          rows={query.data ?? []}
          meta={meta}
          rowKey={(row) => row.id}
          onSelect={(row) => navigate(`/journal?prediction=${encodeURIComponent(row.id)}`)}
          empty={<EmptyState title="Актуальных прогнозов нет" />}
        />
      )}
    </Panel>
  )
}
