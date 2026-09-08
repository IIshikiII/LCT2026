/**
 * Журнал прогнозов — основная рабочая таблица диспетчера (spec §9).
 *
 * Колонки — из `meta.journalColumns` через реестр. Фильтры, сортировка,
 * страница и выбранный прогноз — в URL, поэтому ссылка на текущее состояние
 * передаётся коллеге как есть.
 */
import { usePredictions } from '@/shared/api/queries'
import type { AppMeta } from '@/shared/api/types'
import { csvFilename, downloadCsv, toCsv } from '@/shared/lib/csv'
import { usePredictionFilters, useSelected } from '@/shared/lib/urlState'
import { Button } from '@/shared/ui/Button'
import { Icon } from '@/shared/ui/Icon'
import { EmptyState, ErrorState, TableSkeleton } from '@/shared/ui/states'
import { DataTable } from '@/table/DataTable'
import { Pagination } from '@/table/Pagination'
import { csvColumnsFor, predictionColumns, resolveColumns } from '@/table/columnRegistry'
import { FilterBar } from '../common/FilterBar'

export function JournalScreen({ meta }: { meta: AppMeta }) {
  const api = usePredictionFilters()
  const [selected, select] = useSelected('prediction')
  const query = usePredictions(api.filters)

  const columns = resolveColumns(meta.journalColumns, predictionColumns)
  const rows = query.data?.items ?? []

  const exportCsv = () => {
    const csvColumns = csvColumnsFor(meta.journalColumns, meta)
    downloadCsv(csvFilename('прогнозы'), toCsv(rows, csvColumns))
  }

  return (
    <div className="flex h-full flex-col">
      <FilterBar
        api={api}
        seenDirections={rows.map((row) => row.direction)}
        actions={
          <Button
            size="sm"
            kind="ghost"
            icon={<Icon name="download" size={14} />}
            disabled={rows.length === 0}
            onClick={exportCsv}
          >
            Выгрузить CSV
          </Button>
        }
      />

      <div className="min-h-0 flex-1">
        {query.isPending ? (
          <TableSkeleton rows={12} />
        ) : query.isError ? (
          <ErrorState
            title="Не удалось загрузить журнал прогнозов"
            error={query.error}
            onRetry={() => void query.refetch()}
          />
        ) : (
          <DataTable
            caption="Журнал прогнозов"
            columns={columns}
            rows={rows}
            meta={meta}
            rowKey={(row) => row.id}
            selectedKey={selected}
            onSelect={(row) => select(row.id)}
            sort={api.filters.sort}
            onSort={api.toggleSort}
            empty={
              <EmptyState
                title="Прогнозов по этим условиям нет"
                hint={
                  api.isActive
                    ? 'Снимите часть фильтров или расширьте период.'
                    : 'Данные ещё не рассчитаны — обновление раз в минуту.'
                }
                action={
                  api.isActive ? (
                    <Button size="sm" onClick={api.reset}>
                      Сбросить фильтры
                    </Button>
                  ) : null
                }
              />
            }
          />
        )}
      </div>

      <Pagination
        page={query.data?.page ?? 1}
        total={query.data?.total ?? 0}
        pageSize={query.data?.pageSize}
        onChange={api.setPage}
      />
    </div>
  )
}
