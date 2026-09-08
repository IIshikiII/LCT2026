/**
 * Карта объектов на MapLibre.
 *
 * Компонент императивный по необходимости: MapLibre держит собственное дерево,
 * и оборачивать каждый слой в React-компонент здесь дороже, чем полезнее.
 * Правило простое — карта создаётся один раз, дальше меняются только данные
 * источников и выражения раскраски.
 *
 * Тестами не покрыта: MapLibre требует WebGL, которого в jsdom нет (ADR 0008).
 * Поэтому вся логика фильтров и выбора вынесена в общие хелперы URL-состояния,
 * а здесь остаётся только отрисовка.
 */
import 'maplibre-gl/dist/maplibre-gl.css'
import {
  Map as MapLibreMap,
  NavigationControl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
} from 'maplibre-gl'
import { useEffect, useRef } from 'react'
import type { AppMeta, FacilityCollection } from '@/shared/api/types'
import {
  MAP_LINE,
  MOSCOW_CENTER,
  levelColorExpression,
  mapStyle,
  radiusExpression,
} from './mapStyle'

const POINTS = 'facilities'
const LINES = 'collector-lines'

export interface MapViewProps {
  data: FacilityCollection | undefined
  meta: AppMeta
  selectedId?: string
  onSelect: (predictionId: string) => void
}

export function MapView({ data, meta, selectedId, onSelect }: MapViewProps) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<InstanceType<typeof MapLibreMap> | null>(null)
  const ready = useRef(false)
  // Обработчик клика меняется вместе с пропсами, а слушатель вешается один раз.
  const selectRef = useRef(onSelect)
  selectRef.current = onSelect

  useEffect(() => {
    if (!container.current || map.current) return

    const instance = new MapLibreMap({
      container: container.current,
      style: mapStyle() as never,
      center: MOSCOW_CENTER,
      zoom: 9.4,
      attributionControl: false,
    })
    instance.addControl(new NavigationControl({ showCompass: false }), 'top-right')

    instance.on('load', () => {
      instance.addSource(LINES, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })
      instance.addLayer({
        id: `${LINES}-layer`,
        type: 'line',
        source: LINES,
        paint: { 'line-color': MAP_LINE, 'line-width': 2 },
      })

      instance.addSource(POINTS, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
        cluster: true,
        clusterRadius: 45,
        clusterMaxZoom: 12,
      })

      instance.addLayer({
        id: 'clusters',
        type: 'circle',
        source: POINTS,
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': '#252e36',
          'circle-stroke-color': '#3e4a55',
          'circle-stroke-width': 1,
          'circle-radius': ['interpolate', ['linear'], ['get', 'point_count'], 2, 12, 60, 24],
        },
      })

      instance.addLayer({
        id: 'cluster-count',
        type: 'symbol',
        source: POINTS,
        filter: ['has', 'point_count'],
        layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 11 },
        paint: { 'text-color': '#e4e9ed' },
      })

      instance.addLayer({
        id: 'points',
        type: 'circle',
        source: POINTS,
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-color': levelColorExpression(meta) as never,
          'circle-radius': radiusExpression as never,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#10151a',
        },
      })

      instance.on('click', 'points', (event: MapLayerMouseEvent) => {
        const feature = event.features?.[0]
        const id = feature?.properties?.['predictionId']
        if (typeof id === 'string') selectRef.current(id)
      })
      instance.on('mouseenter', 'points', () => {
        instance.getCanvas().style.cursor = 'pointer'
      })
      instance.on('mouseleave', 'points', () => {
        instance.getCanvas().style.cursor = ''
      })

      ready.current = true
      instance.triggerRepaint()
    })

    map.current = instance
    return () => {
      instance.remove()
      map.current = null
      ready.current = false
    }
    // Карта создаётся один раз; мета участвует только в первичной раскраске,
    // её обновление обрабатывается отдельным эффектом ниже.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* Данные источников. */
  useEffect(() => {
    const instance = map.current
    if (!instance || !data) return

    const apply = () => {
      const points = instance.getSource(POINTS) as GeoJSONSource | undefined
      const lines = instance.getSource(LINES) as GeoJSONSource | undefined
      points?.setData({ type: 'FeatureCollection', features: data.features } as never)
      if (data.lines) lines?.setData(data.lines as never)
    }

    if (ready.current) apply()
    else instance.once('idle', apply)
  }, [data])

  /* Раскраска: мета могла приехать позже карты или измениться. */
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready.current || !instance.getLayer('points')) return
    instance.setPaintProperty('points', 'circle-color', levelColorExpression(meta) as never)
  }, [meta])

  /* Подсветка выбранной точки. */
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready.current || !instance.getLayer('points')) return
    instance.setPaintProperty(
      'points',
      'circle-stroke-color',
      selectedId
        ? (['case', ['==', ['get', 'predictionId'], selectedId], '#e4e9ed', '#10151a'] as never)
        : '#10151a',
    )
  }, [selectedId])

  return <div ref={container} className="h-full w-full" data-testid="map-canvas" />
}
