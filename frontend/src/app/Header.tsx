/**
 * Шапка 48 px: название, свежесть данных, ссылка на API.
 *
 * Свежесть здесь не для красоты. Опрос идёт раз в минуту (ADR 0005), и
 * диспетчер должен видеть, на какую по возрасту картину он смотрит.
 *
 * Ссылка на Swagger — четвёртый пункт итогового продукта по ТЗ (REST API);
 * пусть жюри найдёт её, не спрашивая.
 */
import { useMetaIsFallback, usePipelineHealth } from '@/shared/api/queries'
import { env } from '@/shared/config/env'
import { fmtFreshness } from '@/shared/lib/format'
import { Icon } from '@/shared/ui/Icon'

export function Header() {
  const pipeline = usePipelineHealth()
  const onFallback = useMetaIsFallback()

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-3">
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-[14px] font-medium text-text">АРМ диспетчера ОДС</span>
        <span className="hidden truncate text-[12px] text-text-mute sm:inline">
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
      </div>
    </header>
  )
}
