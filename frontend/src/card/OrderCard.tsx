/**
 * Карточка заявки в правой панели.
 *
 * Устроена так же, как карточка прогноза: шапка из полей сущности плюс те же
 * `actions` и та же панель действий. Отличие одно — у закрытой заявки
 * показывается разметка `outcome`, из которой считается качество модели.
 */
import { Link } from 'react-router-dom'
import { useOrder, useOrderAction } from '@/shared/api/queries'
import type { AppMeta } from '@/shared/api/types'
import { fmtDateTime, fmtDueIn, fmtFacilityPath, isOverdue } from '@/shared/lib/format'
import { statusLabel } from '@/shared/lib/risk'
import { Badge } from '@/shared/ui/Badge'
import { Mono } from '@/shared/ui/Mono'
import { ErrorState, Spinner } from '@/shared/ui/states'
import { ActionBar } from './actions/ActionBar'

export interface OrderCardProps {
  id: string
  meta: AppMeta
}

export function OrderCard({ id, meta }: OrderCardProps) {
  const query = useOrder(id)
  const action = useOrderAction(id)

  if (query.isPending) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return <ErrorState title="Не удалось загрузить заявку" error={query.error} onRetry={() => void query.refetch()} />
  }

  const order = query.data
  const overdue = isOverdue(order.dueAt) && order.status !== 'CLOSED'
  const cause = (meta.reasons[order.outcome ? findCausesRef(meta, order.outcome.actualCause) : ''] ?? []).find(
    (r) => r.code === order.outcome?.actualCause,
  )

  return (
    <div className="flex h-full flex-col">
      <div className="relative min-h-0 flex-1 overflow-y-auto">
        <header className="border-b border-line px-3 py-3">
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-text-mute">Заявка</span>
            <Mono className="text-text">{order.number}</Mono>
            <Badge className="ml-auto">{statusLabel(order.status, 'order', meta)}</Badge>
          </div>

          <h2 className="mt-2 text-[14px] text-text">{order.facility.address}</h2>
          <p className="text-[12px] text-text-mute">{fmtFacilityPath(order.facility)}</p>

          <dl className="mt-3 flex flex-col gap-1.5 text-[12px]">
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Вид работ</dt>
              <dd className="text-right text-text-dim">{order.workType}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Срок</dt>
              <dd className="text-right">
                <Mono className={overdue ? 'text-risk-high' : 'text-text-dim'}>
                  {fmtDateTime(order.dueAt)}
                </Mono>
                <span className={overdue ? 'ml-2 text-risk-high' : 'ml-2 text-text-mute'}>
                  {fmtDueIn(order.dueAt)}
                </span>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Создана</dt>
              <dd className="text-right">
                <Mono className="text-text-dim">{fmtDateTime(order.createdAt)}</Mono>
              </dd>
            </div>
          </dl>

          <Link
            to={`/journal?prediction=${encodeURIComponent(order.predictionId)}`}
            className="mt-3 flex items-center justify-between gap-2 rounded border border-line bg-sunken px-2 py-1.5 text-[12px] text-text-dim hover:border-line-strong"
          >
            <span>Прогноз, из которого создана заявка</span>
            <Mono>{order.predictionId}</Mono>
          </Link>
        </header>

        {order.outcome ? (
          <section className="border-b border-line px-3 py-3">
            <h3 className="mb-2 text-[12px] font-medium text-text-mute">Результат закрытия</h3>
            <dl className="flex flex-col gap-1.5 text-[13px]">
              <div className="flex justify-between gap-2">
                <dt className="text-text-mute">Фактическая причина</dt>
                <dd className="text-right text-text-dim">
                  {cause?.label ?? order.outcome.actualCause}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-text-mute">Прогноз подтвердился</dt>
                <dd
                  className={
                    order.outcome.predictionConfirmed ? 'text-risk-low' : 'text-text-dim'
                  }
                >
                  {order.outcome.predictionConfirmed ? 'да' : 'нет'}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-text-mute">Дата закрытия</dt>
                <dd className="text-right">
                  <Mono className="text-text-dim">{fmtDateTime(order.outcome.closedAt)}</Mono>
                </dd>
              </div>
            </dl>
            {order.outcome.comment ? (
              <p className="mt-2 text-[13px] text-text-dim">{order.outcome.comment}</p>
            ) : null}
          </section>
        ) : null}
      </div>

      <ActionBar
        key={order.id}
        actions={order.actions}
        meta={meta}
        pending={action.isPending}
        error={action.error}
        onRun={(code, values) => action.mutateAsync({ code, values })}
      />
    </div>
  )
}

/**
 * Справочник причин у каждого направления свой, а в заявке лежит только код
 * причины. Ищем словарь, который этот код содержит, — так подпись находится без
 * ветвления по направлению.
 */
function findCausesRef(meta: AppMeta, code: string): string {
  for (const [ref, options] of Object.entries(meta.reasons)) {
    if (options.some((option) => option.code === code)) return ref
  }
  return ''
}
