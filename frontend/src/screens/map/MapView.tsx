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
  AttributionControl,
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
} from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useRef } from 'react'
import { env } from '@/shared/config/env'
import { useTheme } from '@/shared/lib/theme'
import type { AppMeta, FacilityCollection } from '@/shared/api/types'
import {
  MOSCOW_CENTER,
  levelColorExpression,
  mapColors,
  mapStyle,
  pointStrokeExpression,
  radiusExpression,
} from './mapStyle'

/**
 * Адрес воркера MapLibre задаётся явно, и это не перестраховка.
 *
 * Разбор тайлов идёт в веб-воркере, который MapLibre по умолчанию ищет рядом
 * с собой — `new Worker(new URL('./maplibre-gl-worker.mjs', import.meta.url))`.
 * Соседний файл туда не попадает: в деве предбандлер Vite кладёт в
 * `.vite/deps/` только сам `maplibre-gl.js`, в сборке воркер тоже не
 * эмитится. Запрос уходит в 404 — и карта молча остаётся пустой: источники
 * навсегда числятся незагруженными, `queryRenderedFeatures` отдаёт нули, а в
 * `map.on('error')` не приходит ничего. Единственный след — 404 во вкладке
 * «Сеть».
 *
 * Файл в пакете самодостаточный (19 КБ, без импортов и importScripts),
 * поэтому хватает `?url`: Vite кладёт его в ассеты и в деве, и в сборке.
 */
setWorkerUrl(maplibreWorkerUrl)

const POINTS = 'facilities'
const LINES = 'collector-lines'
const OKRUGS = 'moscow-okrugs'

export interface MapViewProps {
  data: FacilityCollection | undefined
  meta: AppMeta
  selectedId?: string
  onSelect: (predictionId: string) => void
}

export function MapView({ data, meta, selectedId, onSelect }: MapViewProps) {
  const { theme } = useTheme()
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
    // Границы округов — данные OpenStreetMap под ODbL, лицензия требует
    // указания источника.
    instance.addControl(new AttributionControl({ compact: true }), 'bottom-right')

    instance.on('load', () => {
      const colors = mapColors()

      // Округа Москвы: настоящая география, лежит в public/geo и грузится
      // самой MapLibre со своего origin. Слои идут первыми — это подложка,
      // сеть коллекторов рисуется поверх.
      instance.addSource(OKRUGS, {
        type: 'geojson',
        data: env.mapDistrictsUrl,
        attribution: '© OpenStreetMap contributors',
      })
      instance.addLayer({
        id: `${OKRUGS}-fill`,
        type: 'fill',
        source: OKRUGS,
        paint: { 'fill-color': colors.districtFill },
      })
      instance.addLayer({
        id: `${OKRUGS}-line`,
        type: 'line',
        source: OKRUGS,
        paint: { 'line-color': colors.districtLine, 'line-width': 1 },
      })

      instance.addSource(LINES, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })
      instance.addLayer({
        id: `${LINES}-layer`,
        type: 'line',
        source: LINES,
        paint: { 'line-color': colors.line, 'line-width': 2 },
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
          'circle-color': colors.cluster,
          'circle-stroke-color': colors.clusterLine,
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
        paint: { 'text-color': colors.label },
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
          'circle-stroke-color': colors.pointLine,
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

  /*
   * Раскраска. Один эффект на три причины перекрасить: мета могла приехать
   * позже карты, выбор точки сменился, либо переключили тему (ADR 0009).
   * MapLibre хранит цвета строками, поэтому тема не «протекает» в слои сама —
   * значения приходится проставлять заново.
   */
  useEffect(() => {
    const instance = map.current
    if (!instance || !ready.current) return
    const colors = mapColors()

    if (instance.getLayer('background')) {
      instance.setPaintProperty('background', 'background-color', colors.background)
    }
    if (instance.getLayer(`${OKRUGS}-fill`)) {
      instance.setPaintProperty(`${OKRUGS}-fill`, 'fill-color', colors.districtFill)
      instance.setPaintProperty(`${OKRUGS}-line`, 'line-color', colors.districtLine)
    }
    if (instance.getLayer(`${LINES}-layer`)) {
      instance.setPaintProperty(`${LINES}-layer`, 'line-color', colors.line)
    }
    if (instance.getLayer('clusters')) {
      instance.setPaintProperty('clusters', 'circle-color', colors.cluster)
      instance.setPaintProperty('clusters', 'circle-stroke-color', colors.clusterLine)
    }
    if (instance.getLayer('cluster-count')) {
      instance.setPaintProperty('cluster-count', 'text-color', colors.label)
    }
    if (instance.getLayer('points')) {
      instance.setPaintProperty('points', 'circle-color', levelColorExpression(meta) as never)
      instance.setPaintProperty(
        'points',
        'circle-stroke-color',
        pointStrokeExpression(selectedId) as never,
      )
    }
  }, [meta, selectedId, theme])

  return <div ref={container} className="h-full w-full" data-testid="map-canvas" />
}
