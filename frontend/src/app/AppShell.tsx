/**
 * Каркас приложения: шапка, рельс, рабочая зона, правая панель.
 *
 * Здесь же — единственное место, где приложение ждёт `/meta`. Если она не
 * пришла или сломана, `useMeta()` отдаёт запасную конфигурацию, и интерфейс
 * поднимается как ни в чём не бывало (критерий приёмки spec §12).
 *
 * Дев-панели заглушек здесь нет намеренно: она монтируется отдельным корнем в
 * src/main.tsx, чтобы код приложения не импортировал ничего из src/mocks/
 * (ADR 0007).
 */
import { useMeta, useMetaQuery } from '@/shared/api/queries'
import { Spinner } from '@/shared/ui/states'
import { Header } from './Header'
import { Rail } from './Rail'
import { RightPanel } from './RightPanel'
import { AppRoutes } from './router'

export function AppShell() {
  const metaQuery = useMetaQuery()
  const meta = useMeta()

  return (
    <div className="flex h-full flex-col bg-bg text-text">
      <Header />

      <div className="flex min-h-0 flex-1">
        <Rail />

        <main className="min-w-0 flex-1">
          {metaQuery.isPending ? (
            <div className="flex h-full items-center justify-center">
              <Spinner />
            </div>
          ) : (
            <AppRoutes meta={meta} />
          )}
        </main>

        <RightPanel meta={meta} />
      </div>
    </div>
  )
}
