/**
 * Карта объектов (spec §9).
 *
 * Использует ту же панель фильтров и те же параметры URL, что и журнал, —
 * поэтому переход между экранами сохраняет выборку, а ссылка на карту с
 * открытой карточкой восстанавливает то же состояние.
 *
 * Карта держится в границах роли. Точки и трассы режет сервер, подложку
 * округов и первую посадку камеры — эти два свойства.
 */
import { useState } from 'react'
import { useFacilities, useFacilityLines } from '@/shared/api/queries'
import type { AppMeta } from '@/shared/api/types'
import { useAuth } from '@/shared/auth/context'
import { scopeDistrict, seesWholeCompany } from '@/shared/auth/scope'
import { usePredictionFilters, useSelected } from '@/shared/lib/urlState'
import { ErrorState, Spinner } from '@/shared/ui/states'
import { FilterBar } from '../common/FilterBar'
import { MapLegend } from './MapLegend'
import { MapView } from './MapView'

export function MapScreen({ meta }: { meta: AppMeta }) {
  const api = usePredictionFilters()
  const { session } = useAuth()
  const [selected, select] = useSelected('prediction')
  /*
   * Границы видимой области. До первого сообщения от карты запрос уходит без
   * bbox — иначе на первом кадре пришлось бы гадать, что попадает в экран.
   */
  const [bbox, setBbox] = useState<string | undefined>(undefined)
  const query = useFacilities(api.filters, bbox)
  const lines = useFacilityLines()

  const features = query.data?.features ?? []

  return (
    <div className="flex h-full flex-col">
      <FilterBar
        api={api}
        withStatus={false}
        withPeriod={false}
        seenDirections={features
          .map((f) => f.properties?.direction)
          .filter((code): code is string => Boolean(code))}
      />

      <div className="relative min-h-0 flex-1">
        {query.isError ? (
          <ErrorState
            title="Не удалось загрузить объекты"
            error={query.error}
            onRetry={() => void query.refetch()}
          />
        ) : (
          <>
            <MapView
              data={query.data}
              lines={lines.data}
              meta={meta}
              selectedId={selected}
              onSelect={select}
              onBoundsChange={setBbox}
              scopeDistrict={scopeDistrict(session?.user)}
              fitToData={!seesWholeCompany(session?.user)}
            />
            <MapLegend meta={meta} total={features.length} />
            {query.isPending ? (
              <div className="absolute inset-0 flex items-center justify-center bg-bg/60">
                <Spinner />
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
