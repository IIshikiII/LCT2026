/**
 * Карта объектов (spec §9).
 *
 * Использует ту же панель фильтров и те же параметры URL, что и журнал, —
 * поэтому переход между экранами сохраняет выборку, а ссылка на карту с
 * открытой карточкой восстанавливает то же состояние.
 *
 * Карта держится в границах роли. Точки и трассы режет сервер, подложку
 * округов и первую посадку камеры — эти два свойства.
 *
 * Группу объектов экран держит в своём состоянии, а не в URL. Номер группы
 * выдаёт supercluster заново при каждом сдвиге карты, поэтому ссылка с таким
 * номером назавтра привела бы в другое место. Ссылка восстанавливает выбранный
 * прогноз, и этого достаточно.
 */
import { useState } from 'react'
import { useFacilities, useFacilityLines } from '@/shared/api/queries'
import type { AppMeta, FacilityFeature } from '@/shared/api/types'
import { useAuth } from '@/shared/auth/context'
import { scopeDistrict, seesWholeCompany } from '@/shared/auth/scope'
import { usePredictionFilters, useSelected } from '@/shared/lib/urlState'
import { ErrorState, Spinner } from '@/shared/ui/states'
import { FilterBar } from '../common/FilterBar'
import { ClusterPanel } from './ClusterPanel'
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
  /* Объекты раскрытой группы и объект, к которому подведена камера. */
  const [cluster, setCluster] = useState<FacilityFeature[] | null>(null)
  const [focus, setFocus] = useState<{ key: string; center: [number, number] }>()
  const query = useFacilities(api.filters, bbox)
  const lines = useFacilityLines()

  const features = query.data?.features ?? []

  /*
   * Выбор объекта из списка группы. Карточка и наезд камеры идут вместе:
   * список даёт адрес, карточка — прогноз, карта — место.
   *
   * Ключ наезда содержит счётчик, поэтому повторное нажатие по той же строке
   * возвращает камеру на место после того, как её увели рукой.
   */
  const pick = (feature: FacilityFeature) => {
    const { predictionId, facilityId } = feature.properties
    if (predictionId) select(predictionId)
    setFocus({ key: `${facilityId}:${Date.now()}`, center: feature.geometry.coordinates })
  }

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

      <div className="relative min-h-0 flex-1" data-cluster-open={cluster ? 'true' : 'false'}>
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
              onClusterOpen={setCluster}
              {...(focus ? { focus } : {})}
            />
            {cluster ? (
              <ClusterPanel
                items={cluster}
                meta={meta}
                {...(selected ? { selectedId: selected } : {})}
                onPick={pick}
                onClose={() => setCluster(null)}
              />
            ) : null}
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
