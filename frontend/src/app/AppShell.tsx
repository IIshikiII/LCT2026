/**
 * Каркас приложения: шапка, рельс, рабочая зона, правая панель.
 *
 * Перед каркасом стоят ворота. Сессии нет — показывается экран входа, и ни
 * один запрос данных не уходит: ТЗ §11 держит разграничение доступа в
 * обязательных требованиях, а запрос без токена всё равно вернул бы 401.
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
import { useAuth } from '@/shared/auth/context'
import { Spinner } from '@/shared/ui/states'
import { LoginScreen } from '@/screens/login/LoginScreen'
import { Header } from './Header'
import { Rail } from './Rail'
import { RightPanel } from './RightPanel'
import { AppRoutes } from './router'

export function AppShell() {
  const { session } = useAuth()
  if (session === null) return <LoginScreen />
  return <SignedIn />
}

/**
 * Рабочая часть. Отдельным компонентом, потому что хуки данных не должны
 * вызываться до входа: иначе первый запрос уходит без токена.
 */
function SignedIn() {
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
