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
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef } from 'react'
import { env } from '@/shared/config/env'
import { useTheme } from '@/shared/lib/theme'
import type { AppMeta, FacilityCollection, LineCollection } from '@/shared/api/types'
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
 * Подключается он через `?worker&url`, и это не то же самое, что `?url`.
 * Файл воркера в пакете не самодостаточен: он импортирует соседний
 * `maplibre-gl-shared.mjs`. `?url` копирует в сборку только сам воркер, сосед
 * остаётся в node_modules, и модульный воркер не стартует — не может
 * разрешить импорт. В деве это не всплывает, потому что Vite отдаёт файл
 * прямо из пакета, где сосед на месте: поломка видна только на собранной
 * версии. `?worker&url` собирает воркер вместе с зависимостями в один ассет.
 *
 * **Формат воркера — обычный скрипт, не модуль.** Это стоило пустой карты и
 * на стенде, и в деве. MapLibre 6 создаёт воркер как `new Worker(url)`, то
 * есть классическим. Сборка с `worker.format: 'es'` отдавала ES-модуль, и
 * такой воркер не стартовал вовсе.
 *
 * Симптом коварный: ошибки нет ни в консоли, ни в `map.on('error')`. Стиль
 * навсегда остаётся незагруженным (`isStyleLoaded()` отдаёт `false`, список
 * слоёв пуст), потому что `Style` ждёт ответа воркера, а его некому дать.
 * Карта выглядит как чёрный прямоугольник, и единственный след — запрос за
 * `maplibre-gl-worker-*.js`, навсегда застрявший в состоянии pending.
 *
 * Обычный скрипт годится в обоих случаях: его примет и классический воркер, и
 * модульный. Поэтому `vite.config.ts` держит `worker.format: 'iife'`.
 */
setWorkerUrl(maplibreWorkerUrl)

const POINTS = 'facilities'
const LINES = 'collector-lines'
const OKRUGS = 'moscow-okrugs'

/**
 * Округление границ до 0.05° (около 3 км по долготе на широте Москвы).
 *
 * Без него каждый пиксель панорамирования давал бы новый ключ кэша и новый
 * запрос. С запасом в полшага объекты у края экрана приезжают заранее, поэтому
 * при небольшом сдвиге карты подгружать нечего.
 */
const BBOX_STEP = 0.05

function roundBbox(west: number, south: number, east: number, north: number): string {
  const down = (v: number) => Math.floor(v / BBOX_STEP) * BBOX_STEP
  const up = (v: number) => Math.ceil(v / BBOX_STEP) * BBOX_STEP
  return [down(west), down(south), up(east), up(north)].map((v) => v.toFixed(2)).join(',')
}

export interface MapViewProps {
  data: FacilityCollection | undefined
  lines: LineCollection | undefined
  meta: AppMeta
  selectedId?: string
  onSelect: (predictionId: string) => void
  /** Сообщает границы видимой области после того, как карта остановилась. */
  onBoundsChange?: (bbox: string) => void
  /**
   * Район роли, если она ограничена одним районом.
   *
   * Карта оставляет на подложке только его: девять округов тому, кто работает
   * в одном, рисуют сеть, к которой его не допустили.
   */
  scopeDistrict?: string
  /**
   * Подвести камеру под пришедшие объекты один раз.
   *
   * Нужно узким ролям: на обзоре всей Москвы их десяток точек выглядит как
   * пустая карта. Роль, которая видит предприятие, остаётся на общем плане.
   */
  fitToData?: boolean
}

export function MapView({
  data,
  lines,
  meta,
  selectedId,
  onSelect,
  onBoundsChange,
  scopeDistrict,
  fitToData,
}: MapViewProps) {
  const { theme } = useTheme()
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<InstanceType<typeof MapLibreMap> | null>(null)
  const ready = useRef(false)
  // Обработчики меняются вместе с пропсами, а слушатели вешаются один раз.
  const selectRef = useRef(onSelect)
  selectRef.current = onSelect
  const boundsRef = useRef(onBoundsChange)
  boundsRef.current = onBoundsChange
  // Карта создаётся один раз, а район приходит вместе с сессией. Ссылка даёт
  // обработчику загрузки актуальное значение, не пересоздавая карту.
  const districtRef = useRef(scopeDistrict)
  districtRef.current = scopeDistrict
  // Камера подводится к объектам один раз за сессию карты.
  const fitted = useRef(false)

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
      // Роль, ограниченная районом, видит на подложке только его округ.
      // Код округа лежит в свойствах того же файла, что рисует границы.
      const okrugFilter = districtRef.current
        ? (['==', ['get', 'code'], districtRef.current] as never)
        : undefined

      instance.addLayer({
        id: `${OKRUGS}-fill`,
        type: 'fill',
        source: OKRUGS,
        ...(okrugFilter ? { filter: okrugFilter } : {}),
        paint: { 'fill-color': colors.districtFill },
      })
      instance.addLayer({
        id: `${OKRUGS}-line`,
        type: 'line',
        source: OKRUGS,
        ...(okrugFilter ? { filter: okrugFilter } : {}),
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

    /*
     * Границы видимой области — параметр bbox запроса объектов. Сообщаем их
     * после загрузки и после каждой остановки карты, а не в процессе движения:
     * иначе на каждый кадр панорамирования уходил бы запрос.
     */
    const report = () => {
      const b = instance.getBounds()
      boundsRef.current?.(roundBbox(b.getWest(), b.getSouth(), b.getEast(), b.getNorth()))
    }
    instance.on('load', report)
    instance.on('moveend', report)

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

  /*
   * Первая подгонка камеры под объекты роли.
   *
   * Только один раз: дальше человек двигает карту сам, и перехватывать у него
   * управление на каждом опросе нельзя. Отступ оставляет объекты у края видимыми.
   */
  useEffect(() => {
    const instance = map.current
    if (!instance || !fitToData || fitted.current) return

    const points = (data?.features ?? [])
      .map((feature) => feature.geometry?.coordinates)
      .filter((pair): pair is [number, number] => Array.isArray(pair) && pair.length === 2)
    if (points.length === 0) return

    const lons = points.map((pair) => pair[0])
    const lats = points.map((pair) => pair[1])
    fitted.current = true
    instance.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 64, maxZoom: 13, duration: 0 },
    )
  }, [data, fitToData])

  /* Точки объектов. Меняются при каждом опросе и при сдвиге карты. */
  useEffect(() => {
    const instance = map.current
    if (!instance || !data) return

    const apply = () => {
      const points = instance.getSource(POINTS) as GeoJSONSource | undefined
      points?.setData({ type: 'FeatureCollection', features: data.features } as never)
    }

    if (ready.current) apply()
    else instance.once('idle', apply)
  }, [data])

  /* Трассы коллекторов. Приезжают один раз за сессию своей ручкой. */
  useEffect(() => {
    const instance = map.current
    if (!instance || !lines) return

    const apply = () => {
      const source = instance.getSource(LINES) as GeoJSONSource | undefined
      source?.setData(lines as never)
    }

    if (ready.current) apply()
    else instance.once('idle', apply)
  }, [lines])

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
