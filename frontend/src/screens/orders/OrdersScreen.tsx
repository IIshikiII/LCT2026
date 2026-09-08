/**
 * Заявки на превентивное обслуживание — что система создала и что с этим стало
 * (spec §9). Модуль автоматического формирования заявок из описания итогового
 * продукта по ТЗ виден именно здесь.
 *
 * Колонки — из `meta.orderColumns`. Фильтр по статусу и выбранная заявка — в URL.
 */
import { useOrders } from '@/shared/api/queries'
import type { AppMeta } from '@/shared/api/types'
import { statusOptions } from '@/shared/lib/risk'
import { useOrderFilters, useSelected } from '@/shared/lib/urlState'
import { Button } from '@/shared/ui/Button'
import { Chip } from '@/shared/ui/Chip'
import { EmptyState, ErrorState, TableSkeleton } from '@/shared/ui/states'
import { DataTable } from '@/table/DataTable'
import { Pagination } from '@/table/Pagination'
import { orderColumns, resolveColumns } from '@/table/columnRegistry'

export function OrdersScreen({ meta }: { meta: AppMeta }) {
  const api = useOrderFilters()
  const [selected, select] = useSelected('order')
  const query = useOrders(api.filters)

  const rows = query.data?.items ?? []

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel px-3 py-2">
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Статус заявки</legend>
          {statusOptions('order', meta).map((status) => (
            <Chip
              key={status.code}
              label={status.label}
              active={api.filters.status.includes(status.code)}
              onToggle={() => api.toggleStatus(status.code)}
            />
          ))}
        </fieldset>
        {api.isActive ? (
          <Button size="sm" kind="ghost" className="ml-auto" onClick={api.reset}>
            Сбросить фильтры
          </Button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1">
        {query.isPending ? (
          <TableSkeleton rows={12} />
        ) : query.isError ? (
          <ErrorState
            title="Не удалось загрузить заявки"
            error={query.error}
            onRetry={() => void query.refetch()}
          />
        ) : (
          <DataTable
            caption="Заявки на превентивное обслуживание"
            columns={resolveColumns(meta.orderColumns, orderColumns)}
            rows={rows}
            meta={meta}
            rowKey={(row) => row.id}
            selectedKey={selected}
            onSelect={(row) => select(row.id)}
            sort={api.filters.sort}
            onSort={api.toggleSort}
            empty={
              <EmptyState
                title="Заявок по этим условиям нет"
                hint={api.isActive ? 'Снимите фильтр по статусу.' : 'Система ещё не создала заявок.'}
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
