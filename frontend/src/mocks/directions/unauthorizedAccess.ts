/**
 * Направление «Несанкционированный доступ» (ТЗ, пункт 3).
 *
 * Выявление аномальных паттернов доступа и прогнозирование попыток
 * проникновения по данным контактных и объёмных датчиков, СКУД и журналов
 * допусков.
 */
import type { CardBlock, Series } from '@/shared/api/types'
import { CONTRACTORS, FACILITY_KINDS } from '../db/catalog'
import { hoursFrom, iso } from '../db/rng'
import {
  buildDailyCounts,
  factorsBlock,
  keyValueBlock,
  tableBlock,
  timelineBlock,
  type TimelineEvent,
} from './blockKit'
import type { DirectionPlugin, PredictionSeed } from './types'

function accessSeries(seed: PredictionSeed): Series {
  return buildDailyCounts(seed.rnd, seed.computedAt, 30, 0.8, 2 + seed.probability * 6)
}

export const unauthorizedAccess: DirectionPlugin = {
  meta: {
    code: 'UNAUTHORIZED_ACCESS',
    label: 'Несанкционированный доступ',
    shortLabel: 'НД',
    accent: '#a884c0',
    minHorizonHours: 24,
  },

  reasonsRef: 'UNAUTHORIZED_ACCESS',
  reasons: [
    { code: 'INTRUSION', label: 'Проникновение подтверждено' },
    { code: 'NO_PERMIT_WORKS', label: 'Работы без оформленного допуска' },
    { code: 'LOCK_DAMAGED', label: 'Повреждён запорный механизм' },
    { code: 'SENSOR_NOISE', label: 'Срабатывание из-за помех датчика' },
    { code: 'FALSE_POSITIVE', label: 'Ложное срабатывание' },
    { code: 'NO_DEFECT', label: 'Нарушений не выявлено' },
  ],

  workTypes: [
    'Осмотр люка и запорного механизма',
    'Замена замка',
    'Проверка СКУД',
    'Выезд группы реагирования',
  ],

  quality: { precision: 0.68, recall: 0.52 },
  share: 0.18,

  appliesTo: (f) =>
    f.kind === FACILITY_KINDS.hatch || (f.kind === FACILITY_KINDS.chamber && f.hasAccessControl),

  summary: (seed) => {
    const nights = seed.rnd.int(2, 9)
    return `${nights} ночных срабатывания за 30 суток без действующего допуска на работы`
  },

  series: (seed) => [accessSeries(seed)],

  blocks: (seed): CardBlock[] => {
    const rnd = seed.rnd
    const f = seed.facility
    const nightAlarms = rnd.int(2, 9)

    const journal: TimelineEvent[] = Array.from({ length: rnd.int(5, 9) }, () => {
      const at = hoursFrom(seed.computedAt, -rnd.int(1, 30) * 24 - rnd.int(0, 23))
      const authorized = rnd.bool(0.55)
      return {
        at: iso(at),
        title: authorized ? 'Доступ по допуску' : 'Срабатывание без допуска',
        kind: 'access',
        note: authorized ? rnd.pick(CONTRACTORS) : 'Допуск на это время не оформлен',
      }
    })

    const permits = Array.from({ length: rnd.int(0, 2) }, () => {
      const start = hoursFrom(seed.computedAt, rnd.int(-48, 48))
      return {
        permit: `ДП-${rnd.int(10000, 99999)}`,
        organization: rnd.pick(CONTRACTORS),
        from: iso(start),
        to: iso(hoursFrom(start, rnd.int(4, 12))),
      }
    })

    return [
      factorsBlock(
        'Почему модель так решила',
        [
          {
            label: 'Ночные срабатывания за 30 суток',
            weight: Math.min(0.9, nightAlarms * 0.11),
            value: `${nightAlarms}`,
          },
          {
            label: 'Срабатывания без действующего допуска',
            weight: 0.44 + seed.probability * 0.35,
            value: `${rnd.int(1, 6)}`,
          },
          {
            label: 'Серия событий на соседних объектах',
            weight: rnd.bool(0.5) ? 0.37 : 0.12,
            value: `${rnd.int(0, 4)} объекта`,
          },
          {
            label: 'Наличие СКУД',
            weight: f.hasAccessControl ? -0.45 : 0.3,
            value: f.hasAccessControl ? 'установлен' : 'отсутствует',
          },
          {
            label: 'Состояние запорного механизма',
            weight: f.inspectionScore >= 4 ? -0.3 : 0.35,
            value: `${f.inspectionScore} из 5`,
          },
          { label: 'Удалённость от поста охраны', weight: 0.19, value: `${rnd.int(1, 9)} км` },
        ],
        'Полоса вправо повышает риск, влево снижает, длина показывает силу влияния.',
      ),

      timelineBlock('Журнал доступов за 30 суток', journal),

      tableBlock(
        'Действующие допуски на объект',
        [
          { key: 'permit', header: 'Допуск' },
          { key: 'organization', header: 'Организация' },
          { key: 'from', header: 'С' },
          { key: 'to', header: 'По' },
        ],
        permits,
      ),

      keyValueBlock('Параметры объекта', [
        { label: 'СКУД', value: f.hasAccessControl },
        { label: 'Оценка обследования', value: `${f.inspectionScore} из 5` },
        { label: 'Ремонтов', value: f.repairCount },
        { label: 'Последний ремонт', value: f.lastRepairAt ?? null },
      ]),
    ]
  },
}
