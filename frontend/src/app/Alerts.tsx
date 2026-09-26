/**
 * Уведомления о тревоге: плашка в шапке и всплывающее окно. ТЗ §10, ADR 0019.
 *
 * Сервер отдаёт действующие тревоги (`GET /alerts`) с готовым текстом,
 * фильтром журнала и шагом повтора. Фронт не знает, какой уровень и какой
 * статус тревожны, и ничего не решает сам: он показывает то, что пришло.
 *
 * Два места, потому что у них разные задачи:
 * - плашка в шапке видна всё время, пока тревога жива, и ведёт в журнал;
 * - всплывающее окно под шапкой справа зовёт действовать. Оно появляется
 *   сразу, когда тревога возникла или инцидентов стало больше, и возвращается
 *   через шаг повтора после «Скрыть». Когда тревоги нет, оба исчезают сами.
 *
 * Число тревожных инцидентов стоит и в заголовке вкладки: диспетчер видит его,
 * даже когда вкладка в фоне.
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAlerts } from '@/shared/api/queries'
import type { Alert, AppMeta } from '@/shared/api/types'
import { type AlertMemory, hide, journalLink, remember } from '@/shared/lib/alertSchedule'
import { cn } from '@/shared/lib/cn'
import { levelColor, levelLabel } from '@/shared/lib/risk'
import { Button } from '@/shared/ui/Button'
import { Icon } from '@/shared/ui/Icon'

/** Как часто перепроверять, не пора ли напомнить. Шаг повтора задаёт сервер. */
const TICK_MS = 15_000
const BASE_TITLE = document.title
const NO_ALERTS: Alert[] = []

/** Плашка в шапке: число тревожных инцидентов и ссылка на них. */
export function AlertBadge({ meta }: { meta: AppMeta }) {
  const alerts = useAlerts().data ?? NO_ALERTS
  if (alerts.length === 0) return null
  return (
    <>
      {alerts.map((alert) => (
        <Link
          key={alert.code}
          to={journalLink(alert.filter)}
          title={alert.title}
          className="flex shrink-0 items-center gap-2 rounded border border-line-strong bg-sunken px-2 py-0.5 text-[12px] whitespace-nowrap text-text outline-offset-2 hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong"
        >
          <span
            aria-hidden="true"
            className="inline-block h-3.5 w-[3px] rounded-[1px]"
            style={{ background: levelColor(alert.level, meta) }}
          />
          <Icon name="bell" size={13} />
          <span>
            {levelLabel(alert.level, meta)}: <span className="mono">{alert.count}</span> не в работе
          </span>
        </Link>
      ))}
    </>
  )
}

/** Всплывающие уведомления под шапкой справа. */
export function AlertToasts({ meta }: { meta: AppMeta }) {
  const query = useAlerts()
  const alerts = query.data ?? NO_ALERTS
  const [now, setNow] = useState(() => Date.now())
  const [state, setState] = useState<{ key: string; memory: Record<string, AlertMemory> }>({
    key: '',
    memory: {},
  })
  const navigate = useNavigate()

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS)
    return () => window.clearInterval(timer)
  }, [])

  // Память пересчитывается на каждый ответ сервера и на каждый тик часов.
  // Пересчёт идёт во время рендера, когда сменился ключ: так React советует
  // хранить то, что выводится из прошлых значений, без эффекта.
  const key = `${query.dataUpdatedAt}:${now}`
  if (state.key !== key) {
    const next: Record<string, AlertMemory> = {}
    for (const alert of alerts) {
      next[alert.code] = remember(state.memory[alert.code], alert.count, alert.repeatMinutes, now)
    }
    setState({ key, memory: next })
  }
  const memory = state.memory

  const total = alerts.reduce((sum, alert) => sum + alert.count, 0)
  useEffect(() => {
    document.title = total > 0 ? `(${total}) ${BASE_TITLE}` : BASE_TITLE
    return () => {
      document.title = BASE_TITLE
    }
  }, [total])

  const shown = alerts.filter((alert) => memory[alert.code] && !memory[alert.code]!.hidden)
  if (shown.length === 0) return null

  function dismiss(code: string) {
    setState((prev) =>
      prev.memory[code]
        ? { ...prev, memory: { ...prev.memory, [code]: hide(prev.memory[code]!, Date.now()) } }
        : prev,
    )
  }

  return (
    <div className="pointer-events-none fixed top-14 right-3 z-50 flex w-[360px] max-w-[calc(100vw-24px)] flex-col gap-2">
      {shown.map((alert) => (
        <Toast
          key={`${alert.code}-${memory[alert.code]!.pulse}`}
          alert={alert}
          meta={meta}
          onOpen={() => {
            dismiss(alert.code)
            void navigate(journalLink(alert.filter))
          }}
          onHide={() => dismiss(alert.code)}
        />
      ))}
    </div>
  )
}

function Toast({
  alert,
  meta,
  onOpen,
  onHide,
}: {
  alert: Alert
  meta: AppMeta
  onOpen: () => void
  onHide: () => void
}) {
  return (
    <section
      role="alert"
      aria-live="assertive"
      className={cn(
        'pointer-events-auto flex gap-3 rounded border border-line-strong bg-panel py-2.5 pr-2.5 pl-0',
        'motion-safe:animate-[alert-in_140ms_ease-out]',
      )}
    >
      <span
        aria-hidden="true"
        className="w-[3px] shrink-0 rounded-r-[1px]"
        style={{ background: levelColor(alert.level, meta) }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 shrink-0" style={{ color: levelColor(alert.level, meta) }}>
            <Icon name="bell" size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[14px] font-medium text-text">{alert.title}</p>
            {alert.hint ? <p className="mt-0.5 text-[12px] text-text-dim">{alert.hint}</p> : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" kind="primary" onClick={onOpen} className="whitespace-nowrap">
            Открыть в журнале
          </Button>
          <Button size="sm" kind="ghost" onClick={onHide} className="whitespace-nowrap">
            Напомнить через {alert.repeatMinutes} мин
          </Button>
        </div>
      </div>
    </section>
  )
}
