/**
 * Справочники инфраструктуры: районы, коллекторы, участки, виды объектов.
 *
 * Здесь живёт предметная область — и только здесь (ADR 0007). Приложение видит
 * всё это как строки, приходящие из API.
 *
 * Координаты синтетические: восемь ломаных в пределах МКАД. Настоящей геометрии
 * коллекторов у нас нет, а правдоподобный рисунок сети нужен, чтобы карта
 * читалась (ADR 0008).
 */
import { makeRng } from './rng'

export interface District {
  code: string
  label: string
  /** центр района — вокруг него раскладываются коллекторы */
  lat: number
  lon: number
}

export const DISTRICTS: District[] = [
  { code: 'CAO', label: 'Центральный', lat: 55.7558, lon: 37.6173 },
  { code: 'SAO', label: 'Северный', lat: 55.8385, lon: 37.5361 },
  { code: 'VAO', label: 'Восточный', lat: 55.787, lon: 37.7756 },
  { code: 'YUAO', label: 'Южный', lat: 55.6215, lon: 37.6541 },
  { code: 'ZAO', label: 'Западный', lat: 55.7281, lon: 37.4436 },
]

export interface Collector {
  code: string
  label: string
  district: string
  /** ломаная [lon, lat] — так же, как в GeoJSON */
  line: [number, number][]
}

export interface Section {
  code: string
  label: string
  collector: string
  district: string
  /** индекс точки на ломаной коллектора */
  anchor: number
}

const COLLECTOR_NAMES = [
  'Тверской',
  'Замоскворецкий',
  'Лефортовский',
  'Дорогомиловский',
  'Останкинский',
  'Сокольнический',
  'Даниловский',
  'Кунцевский',
]

/** Строит ломаную из центра района: правдоподобные изгибы, шаг ~300–600 м. */
function buildLine(seed: string, lat: number, lon: number, points: number): [number, number][] {
  const rnd = makeRng(seed)
  const line: [number, number][] = []
  let curLat = lat + rnd.float(-0.02, 0.02, 5)
  let curLon = lon + rnd.float(-0.03, 0.03, 5)
  let heading = rnd.float(0, Math.PI * 2, 4)

  for (let i = 0; i < points; i += 1) {
    line.push([Number(curLon.toFixed(5)), Number(curLat.toFixed(5))])
    heading += rnd.float(-0.6, 0.6, 3)
    const step = rnd.float(0.004, 0.009, 5)
    curLat += Math.sin(heading) * step * 0.6
    curLon += Math.cos(heading) * step
  }
  return line
}

export const COLLECTORS: Collector[] = COLLECTOR_NAMES.map((name, index) => {
  const district = DISTRICTS[index % DISTRICTS.length] as District
  return {
    code: `COL-${String(index + 1).padStart(2, '0')}`,
    label: `Коллектор «${name}»`,
    district: district.code,
    line: buildLine(`collector-${index}`, district.lat, district.lon, 14),
  }
})

/** ~90 участков: по 11–12 на коллектор. */
export const SECTIONS: Section[] = COLLECTORS.flatMap((collector, ci) => {
  const rnd = makeRng(`sections-${collector.code}`)
  const count = rnd.int(10, 13)
  return Array.from({ length: count }, (_, si) => ({
    code: `SEC-${String(ci + 1).padStart(2, '0')}-${String(si + 1).padStart(2, '0')}`,
    label: `Участок ${si + 1}`,
    collector: collector.code,
    district: collector.district,
    anchor: Math.min(si, collector.line.length - 1),
  }))
})

/** Виды объектов инфраструктуры. Именно по ним направления выбирают, что им подходит. */
export const FACILITY_KINDS = {
  sensor: 'SENSOR',
  chamber: 'CHAMBER',
  hatch: 'HATCH',
  ventShaft: 'VENT_SHAFT',
  pump: 'PUMP',
  structure: 'STRUCTURE',
} as const

export type FacilityKind = (typeof FACILITY_KINDS)[keyof typeof FACILITY_KINDS]

export const FACILITY_KIND_LABELS: Record<string, string> = {
  SENSOR: 'Датчик',
  CHAMBER: 'Камера',
  HATCH: 'Люк',
  VENT_SHAFT: 'Вентшахта',
  PUMP: 'Насос',
  STRUCTURE: 'Строительная конструкция',
}

/** Типы датчиков СМВУ из ТЗ. */
export const SENSOR_TYPES = ['CONTACT', 'VOLUME', 'TEMPERATURE', 'SMOKE', 'GAS'] as const

export const SENSOR_TYPE_LABELS: Record<string, string> = {
  CONTACT: 'Контактный',
  VOLUME: 'Объёмный',
  TEMPERATURE: 'Температурный',
  SMOKE: 'Дымовой',
  GAS: 'Газовый',
}

/** Организации из реестра допусков АРМ-Контроль. */
export const CONTRACTORS = [
  'МОЭСК-Сети',
  'Мосводоканал, участок 4',
  'СпецКабельМонтаж',
  'ТеплоРемонт-7',
  'Москоллектор, бригада 12',
  'ГорСвязьСтрой',
]

export const districtLabel = (code: string): string =>
  DISTRICTS.find((d) => d.code === code)?.label ?? code

export const collectorLabel = (code: string): string =>
  COLLECTORS.find((c) => c.code === code)?.label ?? code

export const sectionLabel = (code: string): string =>
  SECTIONS.find((s) => s.code === code)?.label ?? code
