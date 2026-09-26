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
  type GeoJSONSource,
  type MapLayerMouseEvent,
} from 'maplibre-gl'
import { useEffect, useRef } from 'react'
import { env } from '@/shared/config/env'
import { useTheme } from '@/shared/lib/theme'
import type {
  AppMeta,
  FacilityCollection,
  FacilityFeature,
  LineCollection,
} from '@/shared/api/types'
import {
  MOSCOW_CENTER,
  clusterRadiusExpression,
  clusterStrokeExpression,
  clusterStrokeWidthExpression,
  districtHoverExpression,
  districtLineWidthExpression,
  levelColorExpression,
  mapColors,
  mapStyle,
  pointStrokeExpression,
  radiusExpression,
} from './mapStyle'

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
  /**
   * Объекты кружка, по которому нажали.
   *
   * Разбирает кружок сама карта: список его объектов знает только источник
   * MapLibre. Экран получает готовый список и решает, что с ним делать.
   */
  onClusterOpen?: (items: FacilityFeature[]) => void
  /**
   * Объект, к которому надо подвести камеру.
   *
   * Наезд делается на каждую смену `key`, а не координат: два нажатия подряд
   * по одной и той же строке списка обязаны сработать оба раза.
   */
  focus?: { key: string; center: [number, number] }
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
  onClusterOpen,
  focus,
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
  const clusterRef = useRef(onClusterOpen)
  clusterRef.current = onClusterOpen
  /*
   * Что сейчас под курсором. В ссылках, а не в состоянии React: перерисовка на
   * каждое движение мыши по карте не нужна и стоила бы кадров. Слои
   * перекрашиваются точечно, прямо из обработчика.
   */
  const hoveredDistrict = useRef<string | undefined>(undefined)
  const hoveredCluster = useRef<number | undefined>(undefined)

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
      /*
       * Подсветка округа отдельным слоем поверх заливки, а не сменой её цвета.
       * Заливка непрозрачная и служит подложкой: перекрасить её значило бы
       * подобрать второй цвет суши, который в обеих темах остаётся сушей.
       * Полупрозрачный слой поверх решает это одним значением.
       */
      instance.addLayer({
        id: `${OKRUGS}-hover`,
        type: 'fill',
        source: OKRUGS,
        ...(okrugFilter ? { filter: okrugFilter } : {}),
        paint: {
          'fill-color': colors.hover,
          'fill-opacity': districtHoverExpression(undefined) as never,
        },
      })
      instance.addLayer({
        id: `${OKRUGS}-line`,
        type: 'line',
        source: OKRUGS,
        ...(okrugFilter ? { filter: okrugFilter } : {}),
        paint: {
          'line-color': colors.districtLine,
          'line-width': districtLineWidthExpression(undefined) as never,
        },
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
          'circle-stroke-color': clusterStrokeExpression(undefined) as never,
          'circle-stroke-width': clusterStrokeWidthExpression(undefined) as never,
          'circle-radius': clusterRadiusExpression(undefined) as never,
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

      /* --- подсветка округа под курсором --- */

      const paintDistrict = (code: string | undefined) => {
        if (hoveredDistrict.current === code) return
        hoveredDistrict.current = code
        instance.setPaintProperty(
          `${OKRUGS}-hover`,
          'fill-opacity',
          districtHoverExpression(code) as never,
        )
        instance.setPaintProperty(
          `${OKRUGS}-line`,
          'line-width',
          districtLineWidthExpression(code) as never,
        )
      }

      // Слушаем mousemove, а не mouseenter: округа лежат встык, и при переходе
      // границы mouseenter второго округа приходит без mouseleave первого.
      instance.on('mousemove', `${OKRUGS}-fill`, (event: MapLayerMouseEvent) => {
        const code = event.features?.[0]?.properties?.['code']
        paintDistrict(typeof code === 'string' ? code : undefined)
      })
      instance.on('mouseleave', `${OKRUGS}-fill`, () => paintDistrict(undefined))

      /* --- подсветка и разбор кружка --- */

      const paintCluster = (clusterId: number | undefined) => {
        if (hoveredCluster.current === clusterId) return
        hoveredCluster.current = clusterId
        instance.setPaintProperty(
          'clusters',
          'circle-stroke-color',
          clusterStrokeExpression(clusterId) as never,
        )
        instance.setPaintProperty(
          'clusters',
          'circle-stroke-width',
          clusterStrokeWidthExpression(clusterId) as never,
        )
        instance.setPaintProperty(
          'clusters',
          'circle-radius',
          clusterRadiusExpression(clusterId) as never,
        )
      }

      instance.on('mousemove', 'clusters', (event: MapLayerMouseEvent) => {
        const id = event.features?.[0]?.properties?.['cluster_id']
        paintCluster(typeof id === 'number' ? id : undefined)
        instance.getCanvas().style.cursor = 'pointer'
      })
      instance.on('mouseleave', 'clusters', () => {
        paintCluster(undefined)
        instance.getCanvas().style.cursor = ''
      })

      instance.on('click', 'clusters', (event: MapLayerMouseEvent) => {
        const properties = event.features?.[0]?.properties
        const clusterId = properties?.['cluster_id']
        const count = properties?.['point_count']
        if (typeof clusterId !== 'number' || typeof count !== 'number') return

        const source = instance.getSource(POINTS) as GeoJSONSource | undefined
        if (!source) return

        /*
         * Список объектов кружка знает только источник, и отдаёт он его
         * обещанием: разбор идёт в том же воркере, что собирает кружки.
         * Отказ гасим молча — карта остаётся рабочей, а поднимать диалог на
         * неудавшееся раскрытие группы не за что.
         */
        void source
          .getClusterLeaves(clusterId, count, 0)
          .then((leaves) => clusterRef.current?.(leaves as unknown as FacilityFeature[]))
          .catch(() => undefined)
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

  /*
   * Наезд на объект, выбранный в списке группы.
   *
   * Зависимость — ключ, а не координаты: одна и та же строка списка может
   * выбираться повторно, и камеру надо возвращать на место каждый раз.
   * Масштаб 15 разводит группу на отдельные точки, поэтому объект виден
   * сам, а не кружком.
   */
  useEffect(() => {
    const instance = map.current
    if (!instance || !focus) return
    instance.flyTo({ center: focus.center, zoom: 15, duration: 600 })
    // Координаты меняются вместе с ключом, следить за ними отдельно незачем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.key])

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
      instance.setPaintProperty(`${OKRUGS}-hover`, 'fill-color', colors.hover)
    }
    if (instance.getLayer(`${LINES}-layer`)) {
      instance.setPaintProperty(`${LINES}-layer`, 'line-color', colors.line)
    }
    if (instance.getLayer('clusters')) {
      instance.setPaintProperty('clusters', 'circle-color', colors.cluster)
      // Выражение, а не цвет: иначе смена темы гасила бы подсветку кружка,
      // над которым в этот момент стоит курсор.
      instance.setPaintProperty(
        'clusters',
        'circle-stroke-color',
        clusterStrokeExpression(hoveredCluster.current) as never,
      )
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
