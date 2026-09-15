/**
 * Направление «Риск подтопления». В базовый набор заглушек не входит.
 *
 * Существует ровно для одной цели: доказать, что архитектура выдерживает
 * добавление направления без единой правки за пределами src/mocks/
 * (критерий приёмки spec §12, ADR 0002).
 *
 * По умолчанию выключено. Включается тумблером в дев-панели или флагом
 * `devFlags.override({ extraDirection: true })` в тесте.
 *
 * Обрати внимание, чего здесь нет: регистрации компонентов, правок фильтров,
 * новых цветов в теме, изменений дашборда. Файл, строка в ./index.ts — всё.
 */
import type { CardBlock, Series } from '@/shared/api/types'
import { FACILITY_KINDS } from '../db/catalog'
import { buildSeries, factorsBlock, keyValueBlock, timeseriesBlock } from './blockKit'
import { iso } from '../db/rng'
import type { DirectionPlugin, PredictionSeed } from './types'

function waterSeries(seed: PredictionSeed): Series[] {
  return [
    buildSeries(seed.rnd, seed.computedAt, {
      name: 'Уровень воды в приямке',
      unit: 'см',
      hours: 168,
      base: 9,
      daily: 1.5,
      trend: seed.probability * 26,
      noise: 1.2,
      floor: 0,
    }),
  ]
}

export const floodRisk: DirectionPlugin = {
  meta: {
    code: 'FLOOD_RISK',
    label: 'Риск подтопления',
    shortLabel: 'РП',
    accent: '#4a7fb5',
    minHorizonHours: 24,
  },

  reasonsRef: 'FLOOD_RISK',
  reasons: [
    { code: 'GROUNDWATER', label: 'Поступление грунтовых вод' },
    { code: 'PUMP_FAILURE', label: 'Отказ откачивающего насоса' },
    { code: 'PIPE_LEAK', label: 'Течь водопроводной сети' },
    { code: 'NO_DEFECT', label: 'Подтопления не выявлено' },
  ],

  workTypes: ['Проверка приямка и насоса', 'Гидроизоляция', 'Откачка воды'],

  quality: { precision: 0.74, recall: 0.51 },
  share: 0.12,

  appliesTo: (f) => f.kind === FACILITY_KINDS.pump || f.kind === FACILITY_KINDS.chamber,

  summary: (seed) =>
    `уровень воды в приямке вырос на ${(seed.probability * 26).toFixed(0)} см за неделю`,

  series: waterSeries,

  blocks: (seed): CardBlock[] => [
    factorsBlock('Почему модель так решила', [
      {
        label: 'Тренд уровня воды за 7 суток',
        weight: 0.55 + seed.probability * 0.35,
        value: `+${(seed.probability * 26).toFixed(0)} см`,
      },
      { label: 'Наработка насоса с последнего ТО', weight: 0.4, value: `${seed.rnd.int(300, 4000)} ч` },
      { label: 'Осадки за трое суток', weight: 0.31, value: `${seed.rnd.int(4, 48)} мм` },
      { label: 'Состояние гидроизоляции', weight: 0.27, value: `${seed.facility.inspectionScore} из 5` },
    ]),
    timeseriesBlock('Уровень воды, 7 суток', waterSeries(seed), iso(seed.computedAt)),
    keyValueBlock('Справка', [
      { label: 'Направление добавлено', value: 'дев-панелью, без правок кода приложения' },
      { label: 'Компонентов заведено', value: 0 },
    ]),
  ],
}
