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

/**
 * Строит трассу коллектора: коридор через район, а не клубок.
 *
 * Коллектор — подземный тоннель, он тянется на километры по прямому в целом
 * направлению и лишь слегка отклоняется, обходя застройку. Прежний вариант
 * блуждал случайным шагом и укладывался в 3–5 км: на городском масштабе это
 * читалось закорючкой, а все объекты слипались в одно пятно.
 *
 * Теперь берётся хорда через центр района: середина — сам центр, концы
 * разнесены на `half` в обе стороны, поперёк накладывается плавная волна.
 * Длина выходит 11–17 км.
 *
 * Множитель 0.6 у широты — поправка на масштаб: градус широты примерно вдвое
 * длиннее градуса долготы на широте Москвы, иначе трасса вытянулась бы по
 * вертикали. Пределы `half` и амплитуды подобраны так, чтобы вместе с
 * разбросом объектов остаться в границах Москвы, которые проверяет seed.test.ts.
 */
function buildLine(seed: string, lat: number, lon: number, points: number): [number, number][] {
  const rnd = makeRng(seed)
  const heading = rnd.float(0, Math.PI * 2, 4)
  const half = rnd.float(0.09, 0.14, 5)
  const wave = rnd.float(0.006, 0.015, 5)
  const phase = rnd.float(0, Math.PI * 2, 3)

  const line: [number, number][] = []
  for (let i = 0; i < points; i += 1) {
    // -1 в начале трассы, +1 в конце.
    const along = (i / (points - 1)) * 2 - 1
    // Плавное отклонение поперёк хода, к концам сходит на нет.
    const off = Math.sin(phase + along * Math.PI * 1.5) * wave * (1 - Math.abs(along) * 0.5)

    const dLon = Math.cos(heading) * half * along - Math.sin(heading) * off
    const dLat = (Math.sin(heading) * half * along + Math.cos(heading) * off) * 0.6

    line.push([Number((lon + dLon).toFixed(5)), Number((lat + dLat).toFixed(5))])
  }
  return line
}

export const COLLECTORS: Collector[] = COLLECTOR_NAMES.map((name, index) => {
  const district = DISTRICTS[index % DISTRICTS.length] as District
  return {
    code: `COL-${String(index + 1).padStart(2, '0')}`,
    label: `Коллектор «${name}»`,
    district: district.code,
    line: buildLine(`collector-${index}`, district.lat, district.lon, 24),
  }
})

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
