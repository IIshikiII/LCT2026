/**
 * Дашборд рисков (spec §9).
 *
 * Экран не знает, какие виджеты существуют: он берёт коды из
 * `meta.dashboardWidgets` и отдаёт их реестру. Добавление виджета не требует
 * правки этого файла — в этом весь смысл точки расширения №4.
 */
import type { AppMeta } from '@/shared/api/types'
import { WidgetRenderer } from '@/widgets/widgetRegistry'

export function DashboardScreen({ meta }: { meta: AppMeta }) {
  return (
    <div className="relative h-full overflow-y-auto p-3">
      <div className="grid auto-rows-min grid-cols-1 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {meta.dashboardWidgets.map((code) => (
          <WidgetRenderer key={code} code={code} />
        ))}
      </div>
    </div>
  )
}
