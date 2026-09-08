/**
 * Реестр виджетов дашборда — точка расширения №4 (docs/03-extension-points.md).
 *
 * Состав и порядок берутся из `meta.dashboardWidgets`. Неизвестный код рисуется
 * GenericWidget: дашборд не падает и показывает, чего не хватает.
 *
 * Каждый виджет сам грузит свои данные. Дашборд ничего не собирает и не
 * раздаёт сверху — иначе добавление виджета требовало бы правки дашборда.
 */
import type { FC } from 'react'
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary'
import { DirectionSplit } from './DirectionSplit'
import { GenericWidget } from './GenericWidget'
import { ModelMetrics } from './ModelMetrics'
import { OrderCounters } from './OrderCounters'
import { PipelineHealth } from './PipelineHealth'
import { RiskCounters } from './RiskCounters'
import { TopRisks } from './TopRisks'

export const widgetRegistry: Record<string, FC> = {
  'risk-counters': RiskCounters,
  'direction-split': DirectionSplit,
  'top-risks': TopRisks,
  'model-metrics': ModelMetrics,
  'pipeline-health': PipelineHealth,
  'order-counters': OrderCounters,
}

export function WidgetRenderer({ code }: { code: string }) {
  const Widget = widgetRegistry[code]
  if (!Widget) return <GenericWidget code={code} />
  return (
    <ErrorBoundary key={code} fallback={<GenericWidget code={code} />}>
      <Widget />
    </ErrorBoundary>
  )
}
