/**
 * Направление «Износ инфраструктуры» (ТЗ, пункт 4).
 *
 * Прогнозирование необходимости ремонта строительных конструкций, вентшахт,
 * насосов и люков по данным визуальных обследований ОЭ, возрасту оборудования,
 * интенсивности эксплуатации и внешним факторам (погода, сезон).
 */
import type { CardBlock, Series } from '@/shared/api/types'
import { FACILITY_KINDS, FACILITY_KIND_LABELS } from '../db/catalog'
import { hoursFrom, iso } from '../db/rng'
import {
  buildSeries,
  factorsBlock,
  keyValueBlock,
  timelineBlock,
  timeseriesBlock,
  type TimelineEvent,
} from './blockKit'
import type { DirectionPlugin, PredictionSeed } from './types'

const WEAR_KINDS: string[] = [
  FACILITY_KINDS.ventShaft,
  FACILITY_KINDS.pump,
  FACILITY_KINDS.structure,
  FACILITY_KINDS.hatch,
]

function loadSeries(seed: PredictionSeed): Series[] {
  return [
    buildSeries(seed.rnd, seed.computedAt, {
      name: 'Интенсивность эксплуатации',
      unit: '% от номинала',
      hours: 720,
      base: 58,
      daily: 9,
      trend: seed.probability * 22,
      noise: 3,
      floor: 0,
    }),
    buildSeries(seed.rnd, seed.computedAt, {
      name: 'Влажность в камере',
      unit: '%',
      hours: 720,
      base: 62,
      daily: 4,
      trend: seed.probability * 15,
      noise: 2.5,
      floor: 0,
    }),
  ]
}

export const wearOut: DirectionPlugin = {
  meta: {
    code: 'WEAR_OUT',
    label: 'Износ инфраструктуры',
    shortLabel: 'ИИ',
    accent: '#7f9a72',
    minHorizonHours: 24,
  },

  reasonsRef: 'WEAR_OUT',
  reasons: [
    { code: 'STRUCTURE_DEFECT', label: 'Дефект строительной конструкции' },
    { code: 'CORROSION', label: 'Коррозия несущих элементов' },
    { code: 'PUMP_WORN', label: 'Износ насосного оборудования' },
    { code: 'WATER_INGRESS', label: 'Протечка, поступление грунтовых вод' },
    { code: 'PLANNED_WEAR', label: 'Плановый износ, ремонт по регламенту' },
    { code: 'NO_DEFECT', label: 'Дефект не подтверждён' },
  ],

  workTypes: [
    'Визуальное обследование ОЭ',
    'Ремонт строительных конструкций',
    'Замена насосного оборудования',
    'Гидроизоляция участка',
  ],

  quality: { precision: 0.72, recall: 0.54 },
  share: 0.24,

  appliesTo: (f) => WEAR_KINDS.includes(f.kind),

  summary: (seed) => {
    const kind = (FACILITY_KIND_LABELS[seed.facility.kind] ?? 'объект').toLowerCase()
    const years = (
      (seed.computedAt.getTime() - new Date(seed.facility.commissionedAt).getTime()) /
      (365 * 864e5)
    ).toFixed(0)
    return `${kind}: ${years} лет эксплуатации, оценка обследования ${seed.facility.inspectionScore} из 5`
  },

  series: loadSeries,

  blocks: (seed): CardBlock[] => {
    const rnd = seed.rnd
    const f = seed.facility
    const ageYears =
      (seed.computedAt.getTime() - new Date(f.commissionedAt).getTime()) / (365 * 864e5)

    const events: TimelineEvent[] = Array.from({ length: rnd.int(3, 6) }, () => ({
      at: iso(hoursFrom(seed.computedAt, -rnd.int(30, 1400) * 24)),
      title: rnd.pick([
        'Визуальное обследование ОЭ',
        'Текущий ремонт',
        'Капитальный ремонт',
        'Замечание по результатам осмотра',
      ]),
      kind: rnd.pick(['inspection', 'repair']),
      note: rnd.bool(0.5) ? `Оценка ${rnd.int(2, 5)} из 5` : undefined,
    }))

    return [
      factorsBlock('Почему модель так решила', [
        {
          label: 'Возраст оборудования',
          weight: Math.min(0.9, ageYears / 30),
          value: `${ageYears.toFixed(1)} лет`,
        },
        {
          label: 'Наработка',
          weight: Math.min(0.7, f.operatingHours / 250000),
          value: `${Math.round(f.operatingHours).toLocaleString('ru-RU')} ч`,
        },
        {
          label: 'Оценка визуального обследования ОЭ',
          weight: (3 - f.inspectionScore) * 0.22,
          value: `${f.inspectionScore} из 5`,
        },
        {
          label: 'Число ремонтов за срок службы',
          weight: Math.min(0.55, f.repairCount * 0.1),
          value: `${f.repairCount}`,
        },
        {
          label: 'Влажность и сезонные переходы через ноль',
          weight: 0.28 + seed.probability * 0.2,
          value: rnd.pick(['выше нормы', 'на границе нормы']),
        },
        {
          label: 'Соблюдение графика ППР',
          weight: rnd.bool(0.6) ? -0.34 : 0.26,
          value: rnd.bool(0.6) ? 'соблюдается' : 'есть отставание',
        },
      ]),

      keyValueBlock('Паспорт объекта', [
        { label: 'Вид', value: FACILITY_KIND_LABELS[f.kind] ?? f.kind },
        { label: 'Введён в эксплуатацию', value: f.commissionedAt },
        { label: 'Наработка, ч', value: f.operatingHours },
        { label: 'Оценка обследования', value: `${f.inspectionScore} из 5` },
        { label: 'Ремонтов', value: f.repairCount },
        { label: 'Последний ремонт', value: f.lastRepairAt ?? null },
      ]),

      timeseriesBlock('Нагрузка и влажность, 30 суток', loadSeries(seed), iso(seed.computedAt)),

      timelineBlock('Обследования и ремонты', events),
    ]
  },
}
