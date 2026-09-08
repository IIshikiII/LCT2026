/**
 * Ответ GET /meta — описание предметной области, из которого приложение строит
 * весь интерфейс.
 *
 * Собирается из списка активных направлений. Включили пятое — оно появилось в
 * `directions` и в `reasons`, и этого достаточно, чтобы фильтры, карта и
 * дашборд его подхватили. Правок за пределами src/mocks/ не нужно (ADR 0002).
 */
import type { AppMeta } from '@/shared/api/types'
import { activeDirections } from '../directions'
import { REJECTION_REASONS, REJECTION_REASONS_REF } from './actions'
import { DISTRICTS } from './catalog'

export function buildMeta(): AppMeta {
  const plugins = activeDirections()

  const reasons: AppMeta['reasons'] = { [REJECTION_REASONS_REF]: REJECTION_REASONS }
  for (const plugin of plugins) {
    reasons[plugin.reasonsRef] = plugin.reasons
  }

  return {
    directions: plugins.map((p) => p.meta),

    riskLevels: [
      { code: 'LOW', label: 'Низкий', colorVar: '--risk-low', order: 1 },
      { code: 'MEDIUM', label: 'Средний', colorVar: '--risk-medium', order: 2 },
      { code: 'HIGH', label: 'Высокий', colorVar: '--risk-high', order: 3 },
      { code: 'CRITICAL', label: 'Критический', colorVar: '--risk-critical', order: 4 },
    ],

    statuses: [
      { code: 'NEW', label: 'Новый', scope: 'prediction' },
      { code: 'IN_REVIEW', label: 'На рассмотрении', scope: 'prediction' },
      { code: 'ORDER_CONFIRMED', label: 'Заявка подтверждена', scope: 'prediction' },
      { code: 'REJECTED', label: 'Отклонён', scope: 'prediction' },
      { code: 'CLOSED', label: 'Закрыт', scope: 'prediction' },
      { code: 'AUTO_CREATED', label: 'Создана автоматически', scope: 'order' },
      { code: 'CONFIRMED', label: 'Подтверждена', scope: 'order' },
      { code: 'IN_PROGRESS', label: 'В работе', scope: 'order' },
      { code: 'REJECTED', label: 'Отклонена', scope: 'order' },
      { code: 'CLOSED', label: 'Закрыта', scope: 'order' },
    ],

    districts: DISTRICTS.map((d) => ({ code: d.code, label: d.label })),

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

    reasons,
  }
}
