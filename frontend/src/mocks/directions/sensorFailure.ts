/**
 * Направление «Отказ датчика» (ТЗ, пункт 1).
 *
 * Прогнозирует деградацию и выход из строя контактных, объёмных,
 * температурных, дымовых и газовых датчиков СМВУ на основе паттернов ложных
 * сработок, частоты «шума» и истории ремонтов.
 */
import type { CardBlock, Series } from '@/shared/api/types'
import { FACILITY_KINDS, SENSOR_TYPE_LABELS } from '../db/catalog'
import { hoursFrom, iso } from '../db/rng'
import {
  buildDailyCounts,
  factorsBlock,
  keyValueBlock,
  timelineBlock,
  timeseriesBlock,
  type TimelineEvent,
} from './blockKit'
import type { DirectionPlugin, PredictionSeed } from './types'

function alarmSeries(seed: PredictionSeed): Series {
  // Растущая доля ложных сработок — главный наблюдаемый признак деградации.
  const growth = 4 + seed.probability * 9
  return buildDailyCounts(seed.rnd, seed.computedAt, 45, 1.5, growth)
}

export const sensorFailure: DirectionPlugin = {
  meta: {
    code: 'SENSOR_FAILURE',
    label: 'Отказ датчика',
    shortLabel: 'ОД',
    accent: '#6f8fb5',
    minHorizonHours: 24,
  },

  reasonsRef: 'SENSOR_FAILURE',
  reasons: [
    { code: 'SENSOR_DEGRADED', label: 'Деградация чувствительного элемента' },
    { code: 'WIRING', label: 'Обрыв или окисление шлейфа' },
    { code: 'POWER', label: 'Неисправность питания' },
    { code: 'CONTAMINATION', label: 'Загрязнение камеры датчика' },
    { code: 'FALSE_POSITIVE', label: 'Ложное срабатывание, датчик исправен' },
    { code: 'NO_DEFECT', label: 'Дефект не подтверждён' },
  ],

  workTypes: ['Диагностика датчика', 'Замена датчика', 'Чистка и калибровка', 'Ремонт шлейфа'],

  quality: { precision: 0.81, recall: 0.63 },
  share: 0.34,

  appliesTo: (f) => f.kind === FACILITY_KINDS.sensor,

  summary: (seed) => {
    const type = SENSOR_TYPE_LABELS[seed.facility.sensorType ?? ''] ?? 'Датчик'
    const ratio = Math.round((seed.facility.falseAlarmRatio ?? 0.3) * 100)
    return `${type.toLowerCase()} датчик: доля ложных сработок ${ratio} % и рост «шума» за 45 суток`
  },

  series: (seed) => [alarmSeries(seed)],

  blocks: (seed): CardBlock[] => {
    const f = seed.facility
    const rnd = seed.rnd
    const falseRatio = f.falseAlarmRatio ?? 0.3
    const ageYears = (seed.computedAt.getTime() - new Date(f.commissionedAt).getTime()) / (365 * 864e5)

    const events: TimelineEvent[] = []
    for (let i = 0; i < rnd.int(4, 7); i += 1) {
      events.push({
        at: iso(hoursFrom(seed.computedAt, -rnd.int(6, 120) * 24)),
        title: rnd.pick([
          'Выезд по срабатыванию — не подтверждено',
          'Выезд по срабатыванию — подтверждено',
          'Плановая поверка',
          'Чистка камеры датчика',
        ]),
        kind: rnd.pick(['alarm', 'inspection', 'repair']),
        note: rnd.bool(0.4) ? `Бригада ${rnd.int(3, 18)}` : undefined,
      })
    }
    if (f.lastRepairAt) {
      events.push({ at: f.lastRepairAt, title: 'Ремонт по заявке', kind: 'repair' })
    }

    const blocks: CardBlock[] = [
      factorsBlock('Почему модель так решила', [
        {
          label: 'Доля ложных сработок за квартал',
          weight: Math.min(0.95, falseRatio * 1.4),
          value: `${Math.round(falseRatio * 100)} %`,
        },
        {
          label: 'Рост частоты «шума» за 45 суток',
          weight: 0.42 + seed.probability * 0.3,
          value: `×${(1.4 + seed.probability * 2).toFixed(1)}`,
        },
        {
          label: 'Возраст датчика',
          weight: Math.min(0.6, ageYears / 30),
          value: `${ageYears.toFixed(1)} лет`,
        },
        {
          label: 'Число ремонтов',
          weight: Math.min(0.5, f.repairCount * 0.11),
          value: `${f.repairCount}`,
        },
        {
          label: 'Стабильность питания линии',
          weight: -rnd.float(0.1, 0.4, 2),
          value: 'в норме',
        },
        {
          label: 'Результат последней поверки',
          weight: f.inspectionScore >= 4 ? -0.35 : 0.28,
          value: `${f.inspectionScore} из 5`,
        },
      ]),

      timeseriesBlock('Срабатывания за 45 суток', [alarmSeries(seed)], iso(seed.computedAt)),

      keyValueBlock('Паспорт датчика', [
        { label: 'Тип', value: SENSOR_TYPE_LABELS[f.sensorType ?? ''] ?? f.sensorType },
        { label: 'Введён в эксплуатацию', value: f.commissionedAt },
        { label: 'Наработка, ч', value: f.operatingHours },
        { label: 'Ремонтов', value: f.repairCount },
        { label: 'Последний ремонт', value: f.lastRepairAt ?? null },
      ]),

      timelineBlock('История объекта', events),
    ]

    return blocks
  },
}
