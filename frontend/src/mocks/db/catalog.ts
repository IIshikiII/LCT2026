/**
 * Справочники инфраструктуры: округа, коллекторы, участки, виды объектов.
 *
 * Здесь живёт предметная область — и только здесь (ADR 0007). Приложение видит
 * всё это как строки, приходящие из API.
 *
 * География настоящая: округа и их границы — из OpenStreetMap, см. db/geo.ts.
 * Синтетическими остались только трассы коллекторов — подлинной геометрии сети
 * у нас нет, — но каждая проложена внутри своего реального округа (ADR 0008).
 */
import { OKRUGS } from './geo'
import { makeRng } from './rng'

export interface District {
  code: string
  label: string
  /** центр округа — точка, максимально удалённая от его границы */
  lat: number
  lon: number
}

/**
 * Девять округов основной части города. ТиНАО и Зеленоград на подложке есть,
 * но сети там нет: первые — почти сельская территория, второй — анклав в 37 км,
 * коллекторная сеть в них неправдоподобна.
 */
export const DISTRICTS: District[] = OKRUGS.map((okrug) => ({
  code: okrug.code,
  label: okrug.label,
  lat: okrug.center[1],
  lon: okrug.center[0],
}))

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

/** Коллектор назван по местности внутри своего округа. */
const COLLECTOR_NAMES: Record<string, string> = {
  CAO: 'Тверской',
  SAO: 'Тимирязевский',
  SVAO: 'Останкинский',
  SZAO: 'Щукинский',
  VAO: 'Сокольнический',
  YUAO: 'Даниловский',
  YUVAO: 'Лефортовский',
  YUZAO: 'Черёмушкинский',
  ZAO: 'Дорогомиловский',
}

/**
 * По коллектору на округ. Трасса не генерируется на лету: она посчитана один
 * раз внутри настоящего полигона округа и лежит в db/geo.ts готовой ломаной —
 * так коллектор гарантированно не выходит за границу своего округа и не
 * пересекает соседний.
 */
export const COLLECTORS: Collector[] = OKRUGS.map((okrug, index) => ({
  code: `COL-${String(index + 1).padStart(2, '0')}`,
  label: `Коллектор «${COLLECTOR_NAMES[okrug.code] ?? okrug.label}»`,
  district: okrug.code,
  line: okrug.line,
}))

/** ~90 участков: по 11–12 на коллектор. */
export const SECTIONS: Section[] = COLLECTORS.flatMap((collector, ci) => {
  const rnd = makeRng(`sections-${collector.code}`)
  const count = rnd.int(10, 13)
  const last = collector.line.length - 1
  return Array.from({ length: count }, (_, si) => ({
    code: `SEC-${String(ci + 1).padStart(2, '0')}-${String(si + 1).padStart(2, '0')}`,
    label: `Участок ${si + 1}`,
    collector: collector.code,
    district: collector.district,
    // Участки распределены по всей трассе. Раньше здесь стоял Math.min(si, last),
    // и при 24 точках на 10–13 участков вся вторая половина коллектора пустовала.
    anchor: count > 1 ? Math.round((si * (last - 1)) / (count - 1)) : 0,
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
