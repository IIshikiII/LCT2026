/**
 * Запасная мета на случай, когда GET /meta недоступна или отдала мусор.
 *
 * Критерий приёмки (spec §12): поломанная /meta не мешает приложению подняться.
 *
 * Внимание на `directions: []` — это не забывчивость. Состав направлений знает
 * только бэкенд, выдумывать его на фронте нельзя: подписи окажутся неверными.
 * Вместо этого фильтр направлений достраивается из кодов, реально встреченных в
 * данных (`directionOptions` в shared/lib/risk.ts), а неизвестному коду
 * синтезируется мета: подпись = код, цвет = детерминированный хеш кода.
 * Так приложение остаётся рабочим и честным одновременно.
 *
 * Всё остальное здесь — не предметная область, а устройство самого интерфейса
 * (уровни риска, статусы, состав колонок и виджетов), поэтому разумные значения
 * по умолчанию у него быть обязаны.
 */
import type { AppMeta } from '@/shared/api/types'

export const FALLBACK_META: AppMeta = {
  directions: [],

  riskLevels: [
    { code: 'LOW', label: 'Низкий', colorVar: '--risk-low', order: 1 },
    { code: 'MEDIUM', label: 'Средний', colorVar: '--risk-medium', order: 2 },
    { code: 'HIGH', label: 'Высокий', colorVar: '--risk-high', order: 3 },
    { code: 'CRITICAL', label: 'Критический', colorVar: '--risk-critical', order: 4 },
  ],

  statuses: [
    { code: 'NEW', label: 'Новый', scope: 'prediction', colorVar: '--state-attention' },
    { code: 'IN_REVIEW', label: 'На рассмотрении', scope: 'prediction', colorVar: '--state-progress' },
    { code: 'ORDER_CONFIRMED', label: 'Заявка подтверждена', scope: 'prediction', colorVar: '--state-progress' },
    { code: 'REJECTED', label: 'Отклонён', scope: 'prediction', colorVar: '--state-muted', terminal: true },
    { code: 'CLOSED', label: 'Закрыт', scope: 'prediction', colorVar: '--state-done', terminal: true },
    { code: 'AUTO_CREATED', label: 'Создана автоматически', scope: 'order', colorVar: '--state-attention' },
    { code: 'CONFIRMED', label: 'Подтверждена', scope: 'order', colorVar: '--state-progress' },
    { code: 'REJECTED', label: 'Отклонена', scope: 'order', colorVar: '--state-muted', terminal: true },
    { code: 'IN_PROGRESS', label: 'В работе', scope: 'order', colorVar: '--state-progress' },
    { code: 'DONE', label: 'Выполнена', scope: 'order', colorVar: '--state-done', terminal: true },
  ],

  districts: [],

  journalColumns: [
    'risk',
    'computedAt',
    'direction',
    'facility',
    'summary',
    'probability',
    'horizon',
    'status',
    'order',
  ],

  orderColumns: ['number', 'facility', 'workType', 'dueAt', 'orderStatus', 'prediction'],

  dashboardWidgets: [
    'risk-counters',
    'model-metrics',
    'pipeline-health',
    'direction-split',
    'top-risks',
    'order-counters',
  ],

  reasons: {},
}
