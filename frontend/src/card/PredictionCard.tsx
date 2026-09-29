/**
 * Карточка прогноза — то, ради чего диспетчер открывает правую панель.
 *
 * Шапка собрана из полей самого прогноза, тело — исключительно из `blocks`
 * (ADR 0003). Собственной вёрстки под конкретное направление здесь нет и быть
 * не может: карточка одинакова для всех направлений, включая те, о которых
 * фронт узнает завтра.
 */
import { Link } from 'react-router-dom'
import { usePrediction, usePredictionAction } from '@/shared/api/queries'
import type { AppMeta } from '@/shared/api/types'
import { fmtDateTime, fmtDuration, fmtFacilityPath, fmtHours, fmtPct } from '@/shared/lib/format'
import {
  directionAccent,
  directionLabel,
  isTerminalStatus,
  levelColor,
  levelLabel,
  statusColor,
  statusLabel,
} from '@/shared/lib/risk'
import { Badge } from '@/shared/ui/Badge'
import { Mono } from '@/shared/ui/Mono'
import { ErrorState, Spinner } from '@/shared/ui/states'
import { ActionBar } from './actions/ActionBar'
import { BlockList } from './blockRegistry'

export interface PredictionCardProps {
  id: string
  meta: AppMeta
}

export function PredictionCard({ id, meta }: PredictionCardProps) {
  const query = usePrediction(id)
  const action = usePredictionAction(id)

  if (query.isPending) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return <ErrorState title="Не удалось загрузить прогноз" error={query.error} onRetry={() => void query.refetch()} />
  }

  const prediction = query.data

  return (
    <div className="flex h-full flex-col">
      <div className="relative min-h-0 flex-1 overflow-y-auto">
        <header className="border-b border-line px-3 py-3">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-block h-4 w-[3px] rounded-[1px]"
              style={{ background: levelColor(prediction.level, meta) }}
            />
            <Badge color={directionAccent(prediction.direction, meta)}>
              {directionLabel(prediction.direction, meta)}
            </Badge>
            <span className="text-[13px] text-text-dim">{levelLabel(prediction.level, meta)} риск</span>
            <Badge className="ml-auto" color={statusColor(prediction.status, 'prediction', meta)}>
              {statusLabel(prediction.status, 'prediction', meta)}
            </Badge>
          </div>

          <h2 className="mt-2 text-[14px] text-text">{prediction.facility.address}</h2>
          <p className="text-[12px] text-text-mute">{fmtFacilityPath(prediction.facility)}</p>

          <p className="mt-2 text-[13px] text-text-dim">{prediction.summary}</p>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Вероятность</dt>
              <dd>
                <Mono>{fmtPct(prediction.probability)}</Mono>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Горизонт</dt>
              <dd>
                <Mono>{fmtHours(prediction.horizonHours)}</Mono>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Рассчитан</dt>
              <dd>
                <Mono className="text-text-dim">{fmtDateTime(prediction.computedAt)}</Mono>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-text-mute">Время расчёта</dt>
              <dd>
                <Mono className="text-text-dim">{fmtDuration(prediction.computeMs)}</Mono>
              </dd>
            </div>
          </dl>

          {prediction.orderId ? (
            <Link
              to={`/orders?order=${encodeURIComponent(prediction.orderId)}`}
              className="mt-3 flex items-center justify-between gap-2 rounded border border-line bg-sunken px-2 py-1.5 text-[12px] text-text-dim hover:border-line-strong"
            >
              <span>Заявка на превентивное обслуживание</span>
              <span className="flex shrink-0 items-center gap-2">
                {prediction.orderStatus ? (
                  <Badge color={statusColor(prediction.orderStatus, 'order', meta)}>
                    {statusLabel(prediction.orderStatus, 'order', meta)}
                  </Badge>
                ) : null}
                <Mono>{prediction.orderId}</Mono>
              </span>
            </Link>
          ) : prediction.facilityOrderId ? (
            // Автозаявка на объект одна, пока открыта. Свежий прогноз своей не
            // получает и показывает ту, что уже есть.
            <Link
              to={`/orders?order=${encodeURIComponent(prediction.facilityOrderId)}`}
              className="mt-3 flex items-center justify-between gap-2 rounded border border-line bg-sunken px-2 py-1.5 text-[12px] text-text-dim hover:border-line-strong"
            >
              <span>По объекту уже открыта заявка по прошлому прогнозу</span>
              <Mono>{prediction.facilityOrderId}</Mono>
            </Link>
          ) : null}
        </header>

        <BlockList blocks={prediction.blocks} />
      </div>

      <ActionBar
        key={prediction.id}
        actions={prediction.actions}
        meta={meta}
        pending={action.isPending}
        error={action.error}
        onRun={(code, values) => action.mutateAsync({ code, values })}
        emptyText={noActionsText(prediction, meta)}
      />
    </div>
  )
}

/** Почему у прогноза нет действий. Причину знает статус и заявка, а не роль. */
function noActionsText(
  prediction: { status: string; orderId?: string; facilityOrderId?: string },
  meta: AppMeta,
): string {
  if (isTerminalStatus(prediction.status, 'prediction', meta)) {
    return `${statusLabel(prediction.status, 'prediction', meta)}: карточка закрыта и хранится в истории.`
  }
  const order = prediction.orderId ?? prediction.facilityOrderId
  if (order) return `Действий по прогнозу нет: работа идёт по заявке ${order}.`
  return 'Действий по прогнозу для вашей роли нет.'
}
