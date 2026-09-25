/**
 * Направление «Пожарный риск» (ТЗ, пункт 2).
 *
 * Раннее предупреждение о повышенном риске пожара или задымления в коллекторе
 * по динамике температурных и дымовых датчиков и корреляции с графиками
 * сварочных и горячих работ из АРМ-Контроль.
 */
import type { CardBlock, Series } from '@/shared/api/types'
import { CONTRACTORS, FACILITY_KINDS } from '../db/catalog'
import { hoursFrom, iso } from '../db/rng'
import {
  buildSeries,
  factorsBlock,
  tableBlock,
  timeseriesBlock,
  timelineBlock,
  type TimelineEvent,
} from './blockKit'
import type { DirectionPlugin, PredictionSeed } from './types'

function climateSeries(seed: PredictionSeed): Series[] {
  const heat = 2 + seed.probability * 11
  return [
    buildSeries(seed.rnd, seed.computedAt, {
      name: 'Температура',
      unit: '°C',
      hours: 168,
      base: 21,
      daily: 1.8,
      trend: heat,
      noise: 0.5,
    }),
    buildSeries(seed.rnd, seed.computedAt, {
      name: 'Задымление',
      unit: '% затемнения',
      hours: 168,
      base: 1.2,
      daily: 0.4,
      trend: seed.probability * 4.5,
      noise: 0.35,
      floor: 0,
    }),
  ]
}

export const fireRisk: DirectionPlugin = {
  meta: {
    code: 'FIRE_RISK',
    label: 'Пожарный риск',
    shortLabel: 'ПР',
    accent: '#c96a3a',
    minHorizonHours: 24,
  },

  reasonsRef: 'FIRE_RISK',
  reasons: [
    { code: 'HOT_WORKS', label: 'Нарушение при огневых работах' },
    { code: 'CABLE_OVERHEAT', label: 'Перегрев кабельной линии' },
    { code: 'VENTILATION', label: 'Недостаточная вентиляция участка' },
    { code: 'DEBRIS', label: 'Горючий мусор в камере' },
    { code: 'DETECTOR_DIRTY', label: 'Извещатель загрязнён или отсырел' },
    { code: 'COMMISSIONING', label: 'Пусконаладка системы' },
    { code: 'FALSE_POSITIVE', label: 'Ложное срабатывание, риска не было' },
    { code: 'NO_DEFECT', label: 'Дефект не подтверждён' },
  ],

  workTypes: [
    'Осмотр камеры и кабельных линий',
    'Тепловизионное обследование',
    'Проверка вентиляции',
    'Уборка горючих материалов',
    'Осмотр и чистка извещателя',
  ],

  // Экспертные правила, модели нет (бэкенд, ADR 0016). Точность не измерена.
  quality: {
    method: 'expert_rules',
    note:
      'Экспертные правила. Точность не измерена: подтверждённых пожаров в ' +
      'выгрузке нет. Её посчитают отметки бригад при закрытии заявок.',
  },
  share: 0.24,

  appliesTo: (f) => f.kind === FACILITY_KINDS.chamber || f.kind === FACILITY_KINDS.ventShaft,

  summary: (seed) => {
    const delta = (2 + seed.probability * 11).toFixed(1)
    return `рост температуры на ${delta} °C за неделю на фоне огневых работ рядом`
  },

  series: climateSeries,

  blocks: (seed): CardBlock[] => {
    const rnd = seed.rnd
    const f = seed.facility

    // Допуски АРМ-Контроль на ближайшие сутки — они и создают риск.
    const permits = Array.from({ length: rnd.int(1, 3) }, () => {
      const start = hoursFrom(seed.computedAt, rnd.int(-18, 30))
      return {
        organization: rnd.pick(CONTRACTORS),
        workType: rnd.pick(['Сварочные работы', 'Резка металла', 'Пайка муфт', 'Прогрев кабеля']),
        from: iso(start),
        to: iso(hoursFrom(start, rnd.int(3, 10))),
        permit: `ДП-${rnd.int(10000, 99999)}`,
      }
    })

    const events: TimelineEvent[] = Array.from({ length: rnd.int(3, 6) }, () => ({
      at: iso(hoursFrom(seed.computedAt, -rnd.int(2, 90) * 24)),
      title: rnd.pick([
        'Срабатывание дымового датчика',
        'Превышение температуры, проверка',
        'Огневые работы по допуску',
        'Плановый осмотр камеры',
      ]),
      kind: rnd.pick(['alarm', 'work', 'inspection']),
    }))

    return [
      factorsBlock('Почему модель так решила', [
        {
          label: 'Тренд температуры за 7 суток',
          weight: 0.5 + seed.probability * 0.4,
          value: `+${(2 + seed.probability * 11).toFixed(1)} °C`,
        },
        {
          label: 'Показания дымовых датчиков',
          weight: 0.3 + seed.probability * 0.35,
          value: `${(1.2 + seed.probability * 4.5).toFixed(1)} % затемнения`,
        },
        {
          label: 'Огневые работы по допуску в ближайшие сутки',
          weight: permits.length > 1 ? 0.62 : 0.31,
          value: `${permits.length} допуск(а)`,
        },
        {
          label: 'Плотность кабельных линий на участке',
          weight: 0.24,
          value: rnd.pick(['высокая', 'средняя']),
        },
        {
          label: 'Работоспособность вентиляции',
          weight: f.inspectionScore >= 4 ? -0.4 : 0.33,
          value: f.inspectionScore >= 4 ? 'в норме' : 'снижена',
        },
        { label: 'Сезонная поправка', weight: -0.18, value: 'сентябрь' },
      ]),

      timeseriesBlock('Температура и задымление, 7 суток', climateSeries(seed), iso(seed.computedAt)),

      tableBlock(
        'Допуски на работы рядом (АРМ-Контроль)',
        [
          { key: 'permit', header: 'Допуск' },
          { key: 'organization', header: 'Организация' },
          { key: 'workType', header: 'Вид работ' },
          { key: 'from', header: 'Начало' },
          { key: 'to', header: 'Окончание' },
        ],
        permits,
      ),

      timelineBlock('История объекта', events),
    ]
  },
}
