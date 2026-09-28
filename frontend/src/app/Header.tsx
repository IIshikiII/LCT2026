/**
 * Шапка 48 px: название, свежесть данных, ссылка на API.
 *
 * Свежесть здесь не для красоты. Опрос идёт раз в минуту (ADR 0005), и
 * диспетчер должен видеть, на какую по возрасту картину он смотрит.
 *
 * Ссылка на Swagger — четвёртый пункт итогового продукта по ТЗ (REST API);
 * пусть жюри найдёт её, не спрашивая.
 *
 * Роль и область видимости стоят слева, в названии рабочего места. Прежде там
 * стояло «АРМ диспетчера ОДС» при любой роли, и диспетчер района читал чужую
 * должность. Теперь надпись отвечает на вопрос «где я и что мне видно»:
 * пустой журнал у техника объясняется областью, а не поломкой.
 *
 * Имя справа — ответ заказчика 3.6: запись остаётся за тем, кто отработал
 * прогноз, и человек обязан видеть, под кем он работает.
 */
import { useLogout, useMeta, useMetaIsFallback, usePipelineHealth } from '@/shared/api/queries'
import { useAuth } from '@/shared/auth/context'
import { scopeLabel } from '@/shared/auth/scope'
import { env } from '@/shared/config/env'
import { fmtFreshness } from '@/shared/lib/format'
import { Button } from '@/shared/ui/Button'
import { Icon } from '@/shared/ui/Icon'
import { AlertBadge } from './Alerts'

export function Header() {
  const pipeline = usePipelineHealth()
  const onFallback = useMetaIsFallback()
  const meta = useMeta()
  const { session, signOut } = useAuth()
  const logout = useLogout()

  function leave() {
    // Выход не ждёт сервера: запись в журнал уже ушла, а держать человека на
    // экране из-за сетевой ошибки незачем.
    logout.mutate(undefined, { onSettled: signOut })
  }

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-3">
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="shrink-0 text-[14px] font-medium text-text">
          АРМ{session ? ` · ${session.user.roleLabel}` : ''}
        </span>
        {session ? (
          <span
            className="shrink-0 rounded border border-line bg-sunken px-2 py-0.5 text-[12px] text-text-dim"
            title="Что вам видно"
          >
            {scopeLabel(session.user, meta)}
          </span>
        ) : null}
        <span className="hidden truncate text-[12px] text-text-mute lg:inline">
          прогнозирование аварий инженерных коллекторов
        </span>
      </div>

      {onFallback ? (
        <span
          className="flex items-center gap-1.5 rounded border border-line bg-sunken px-2 py-0.5 text-[12px] text-risk-medium"
          title="GET /meta недоступна, интерфейс собран на запасных конфигах"
        >
          <Icon name="warning" size={13} />
          запасная конфигурация
        </span>
      ) : null}

      <div className="ml-auto flex items-center gap-4">
        {session ? <AlertBadge meta={meta} /> : null}
        <span className="text-[12px] text-text-mute" aria-live="polite">
          данные:{' '}
          <span className="mono text-text-dim">
            {pipeline.data ? fmtFreshness(pipeline.data.freshnessMinutes) : '—'}
          </span>
        </span>

        <a
          href={env.apiDocsUrl}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 text-[12px] text-text-dim outline-offset-2 hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong"
        >
          REST API
          <Icon name="external" size={13} />
        </a>

        {session ? (
          <div className="flex items-center gap-2 border-l border-line pl-4">
            <span className="text-[12px] text-text">{session.user.fullName}</span>
            <Button size="sm" kind="ghost" onClick={leave} disabled={logout.isPending}>
              Выйти
            </Button>
          </div>
        ) : null}
      </div>
    </header>
  )
}
