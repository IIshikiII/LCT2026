/**
 * Дашборд рисков (spec §9).
 *
 * Экран не знает, какие виджеты существуют: он берёт коды из
 * `meta.dashboardWidgets` и отдаёт их реестру. Добавление виджета не требует
 * правки этого файла — в этом весь смысл точки расширения №4.
 */
import type { AppMeta } from '@/shared/api/types'
import { WidgetRenderer } from '@/widgets/widgetRegistry'

/**
 * Сетка на 6 колонок. Виджеты занимают 2 или 4 колонки и складываются в ряды
 * без остатка: раньше каждый ряд заканчивался пустой колонкой справа. На
 * среднем экране колонок три, и виджет берёт всю ширину.
 *
 * Высота ряда — по содержимому. Растягивать ряды на весь экран пробовали:
 * короткий список из четырёх строк раздувается на треть высоты, и пустота
 * просто переезжает внутрь панели. Низ экрана добирает «Топ-10»: это таблица,
 * и высокой она выглядит уместно.
 */
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
