/**
 * Объекты инфраструктуры — то, на что делается прогноз.
 *
 * Около 600 штук: на каждый участок приходится камера, люк, три-четыре датчика
 * и иногда вентшахта, насос или строительная конструкция. Такое распределение
 * даёт всем четырём направлениям достаточно подходящих объектов
 * (`appliesTo` в src/mocks/directions/).
 */
import type { FacilityRef } from '@/shared/api/types'
import {
  COLLECTORS,
  FACILITY_KINDS,
  SECTIONS,
  SENSOR_TYPES,
  collectorLabel,
  sectionLabel,
  type Collector,
} from './catalog'
import { NOW, hoursFrom, iso, makeRng } from './rng'

export interface Facility extends FacilityRef {
  /** SENSOR | CHAMBER | HATCH | VENT_SHAFT | PUMP | STRUCTURE */
  kind: string
  /** только для kind = SENSOR */
  sensorType?: string
  commissionedAt: string
  /** наработка, часы — признак для износа */
  operatingHours: number
  lastRepairAt?: string
  repairCount: number
  /** результат последнего визуального обследования ОЭ, 1 (плохо) … 5 (хорошо) */
  inspectionScore: number
  /** доля ложных сработок за последний квартал, только для датчиков */
  falseAlarmRatio?: number
  /** есть ли СКУД на объекте — признак для контроля доступа */
  hasAccessControl: boolean
}

const STREETS = [
  'ул. Пятницкая',
  'Земляной Вал',
  'Пресненская наб.',
  'ул. Госпитальный Вал',
  'Ленинградский пр-т',
  'ул. Русаковская',
  'Варшавское ш.',
  'Кутузовский пр-т',
  'ул. Академика Королёва',
  'Сокольнический Вал',
  'Дубининская ул.',
  'ул. Ивана Франко',
]

function addressFor(rnd: ReturnType<typeof makeRng>): string {
  return `${rnd.pick(STREETS)}, д. ${rnd.int(1, 84)}${rnd.bool(0.25) ? ` стр. ${rnd.int(1, 4)}` : ''}`
}

/**
 * Точка на трассе коллектора внутри участка.
 *
 * `t` — доля пути по сегменту от якоря участка к следующему: объекты участка
 * раскладываются вдоль тоннеля равномерно, а не сваливаются в одну точку.
 * Разброс оставлен маленьким (порядка полусотни метров): камера, люк и датчики
 * стоят у коллектора, а не разбросаны по кварталу.
 */
function pointOnLine(
  collector: Collector,
  anchor: number,
  t: number,
  rnd: ReturnType<typeof makeRng>,
) {
  const line = collector.line
  const index = Math.max(0, Math.min(anchor, line.length - 2))
  const from = line[index] ?? [37.6173, 55.7558]
  const to = line[index + 1] ?? from

  return {
    lon: Number((from[0] + (to[0] - from[0]) * t + rnd.float(-0.0007, 0.0007, 5)).toFixed(5)),
    lat: Number((from[1] + (to[1] - from[1]) * t + rnd.float(-0.0005, 0.0005, 5)).toFixed(5)),
  }
}

function buildFacility(
  id: string,
  kind: string,
  collector: Collector,
  sectionCode: string,
  anchor: number,
  offset: number,
  chamber: string | undefined,
  device: string | undefined,
  sensorType: string | undefined,
): Facility {
  const rnd = makeRng(id)
  const { lat, lon } = pointOnLine(collector, anchor, offset, rnd)
  const ageYears = rnd.float(1, 34, 1)
  const repairCount = rnd.int(0, 6)

  return {
    id,
    kind,
    collector: collectorLabel(collector.code),
    section: sectionLabel(sectionCode),
    chamber,
    device,
    sensorType,
    district: collector.district,
    address: addressFor(rnd),
    lat,
    lon,
    commissionedAt: iso(hoursFrom(NOW, -ageYears * 8760)),
    operatingHours: Math.round(ageYears * 8760 * rnd.float(0.55, 0.95, 3)),
    lastRepairAt: repairCount > 0 ? iso(hoursFrom(NOW, -rnd.int(30, 900) * 24)) : undefined,
    repairCount,
    inspectionScore: rnd.int(1, 5),
    falseAlarmRatio: kind === FACILITY_KINDS.sensor ? rnd.float(0.02, 0.78, 3) : undefined,
    hasAccessControl: kind === FACILITY_KINDS.hatch || kind === FACILITY_KINDS.chamber
      ? rnd.bool(0.7)
      : false,
  }
}

function buildAll(): Facility[] {
  const byCode = new Map(COLLECTORS.map((c) => [c.code, c]))
  const facilities: Facility[] = []
  let counter = 0
  const nextId = () => `F-${String((counter += 1)).padStart(4, '0')}`

  for (const section of SECTIONS) {
    const collector = byCode.get(section.collector)
    if (!collector) continue
    const rnd = makeRng(`facilities-${section.code}`)
    const chamberNo = rnd.int(1, 40)
    const chamber = `Камера ${chamberNo}`

    // Сначала собираем состав участка, потом раскладываем его вдоль трассы:
    // чтобы разнести объекты равномерно, надо заранее знать, сколько их.
    // Порядок обращений к rnd сохранён — иначе поехал бы весь сид.
    const specs: { kind: string; device?: string; sensorType?: string }[] = [
      { kind: FACILITY_KINDS.chamber },
      { kind: FACILITY_KINDS.hatch, device: `Люк ${chamberNo}` },
    ]

    const sensorCount = rnd.int(3, 4)
    for (let i = 0; i < sensorCount; i += 1) {
      const type = rnd.pick(SENSOR_TYPES)
      specs.push({
        kind: FACILITY_KINDS.sensor,
        device: `Датчик ${type}-${String(i + 1).padStart(2, '0')}`,
        sensorType: type,
      })
    }

    if (rnd.bool(0.45)) {
      specs.push({ kind: FACILITY_KINDS.ventShaft, device: `Вентшахта ${rnd.int(1, 9)}` })
    }
    if (rnd.bool(0.3)) {
      specs.push({ kind: FACILITY_KINDS.pump, device: `Насос ${rnd.int(1, 6)}` })
    }
    if (rnd.bool(0.35)) {
      specs.push({ kind: FACILITY_KINDS.structure, device: 'Конструкция свода' })
    }

    specs.forEach((spec, index) => {
      facilities.push(
        buildFacility(
          nextId(),
          spec.kind,
          collector,
          section.code,
          section.anchor,
          (index + 0.5) / specs.length,
          chamber,
          spec.device,
          spec.sensorType,
        ),
      )
    })
  }

  return facilities
}

export const FACILITIES: Facility[] = buildAll()

export const facilityById = new Map(FACILITIES.map((f) => [f.id, f]))

/** В API уезжает только FacilityRef: служебные признаки — дело бэкенда. */
export function toFacilityRef(f: Facility): FacilityRef {
  return {
    id: f.id,
    collector: f.collector,
    section: f.section,
    chamber: f.chamber,
    device: f.device,
    district: f.district,
    address: f.address,
    lat: f.lat,
    lon: f.lon,
  }
}
