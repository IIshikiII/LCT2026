/**
 * Реестр колонок — точка расширения №3 (docs/03-extension-points.md).
 *
 * Состав и порядок колонок приходят из меты (`journalColumns`, `orderColumns`).
 * Неизвестный ключ молча отбрасывается: это нормальный режим, пока фронт
 * догоняет бэкенд, — лучше таблица без одной колонки, чем упавший экран.
 *
 * Ни одна ячейка не ветвится по коду направления: подписи и цвета берутся из
 * меты через shared/lib/risk.ts (ADR 0002).
 */
import { Link } from 'react-router-dom'
import type { AppMeta, Prediction, WorkOrder } from '@/shared/api/types'
import {
  DASH,
  fmtDateTime,
  fmtDueIn,
  fmtFacilityPath,
  fmtHours,
  fmtPct,
  isOverdue,
} from '@/shared/lib/format'
import {
  directionAccent,
  directionLabel,
  isTerminalStatus,
  levelLabel,
  statusColor,
  statusLabel,
} from '@/shared/lib/risk'
import { Badge } from '@/shared/ui/Badge'
import { Mono } from '@/shared/ui/Mono'
import { RiskBar } from '@/shared/ui/RiskBar'
import type { ColumnDef } from './DataTable'

/**
 * Объект с иерархией: адрес сверху, путь по коллектору — приглушённо снизу.
 *
 * Обе строки обрезаются. Полный текст остаётся в подсказке и в выгрузке CSV:
 * колонка выгрузки берёт значения из данных, а не из того, что нарисовано.
 */
function FacilityCell({ value }: { value: Prediction['facility'] }) {
  const path = fmtFacilityPath(value)
  return (
    <span className="flex min-w-0 flex-col leading-tight" title={`${value.address}\n${path}`}>
      <span className="truncate text-text">{value.address}</span>
      <span className="truncate text-[12px] text-text-mute">{path}</span>
    </span>
  )
}

/**
 * Прогноз одной фразой. Две строки, дальше многоточие.
 *
 * Без ограничения строка на пять строк растягивала ряд, и на экране помещалось
 * вдвое меньше прогнозов. Целиком фраза видна в подсказке и в карточке.
 */
function SummaryCell({ text }: { text: string }) {
  return (
    <span className="line-clamp-2 text-text-dim" title={text}>
      {text}
    </span>
  )
}

/** Ссылка на заявку. Открывает экран заявок с раскрытой карточкой. */
function OrderLink({ id }: { id: string }) {
  return (
    <Link
      to={`/orders?order=${encodeURIComponent(id)}`}
      onClick={(event) => event.stopPropagation()}
      className="text-text-dim underline decoration-line-strong underline-offset-2 hover:text-text"
    >
      <Mono>{id}</Mono>
    </Link>
  )
}

export const predictionColumns: Record<string, ColumnDef<Prediction>> = {
  risk: {
    key: 'risk',
    header: '',
    width: 22,
    cell: (row, meta) => <RiskBar level={row.level} meta={meta} />,
  },
  computedAt: {
    key: 'computedAt',
    header: 'Рассчитан',
    width: 132,
    sortable: true,
    cell: (row) => <Mono className="text-text-dim">{fmtDateTime(row.computedAt)}</Mono>,
  },
  direction: {
    key: 'direction',
    header: 'Направление',
    width: 170,
    sortable: true,
    cell: (row, meta) => (
      <Badge
        color={directionAccent(row.direction, meta)}
        title={directionLabel(row.direction, meta)}
      >
        {directionLabel(row.direction, meta)}
      </Badge>
    ),
  },
  facility: {
    key: 'facility',
    header: 'Объект',
    width: 230,
    sortable: true,
    cell: (row) => <FacilityCell value={row.facility} />,
  },
  summary: {
    key: 'summary',
    header: 'Прогноз',
    cell: (row) => <SummaryCell text={row.summary} />,
  },
  probability: {
    key: 'probability',
    header: 'Вероятность',
    width: 108,
    align: 'right',
    sortable: true,
    cell: (row) => <Mono>{fmtPct(row.probability)}</Mono>,
  },
  horizon: {
    key: 'horizon',
    header: 'Горизонт',
    width: 88,
    align: 'right',
    sortable: true,
    cell: (row) => <Mono className="text-text-dim">{fmtHours(row.horizonHours)}</Mono>,
  },
  status: {
    key: 'status',
    header: 'Статус',
    width: 160,
    sortable: true,
    cell: (row, meta) => (
      <Badge color={statusColor(row.status, 'prediction', meta)}>
        {statusLabel(row.status, 'prediction', meta)}
      </Badge>
    ),
  },
  assignee: {
    key: 'assignee',
    header: 'Исполнитель',
    width: 130,
    sortable: true,
    cell: (row) =>
      row.assignee ? (
        <span className="text-text-dim">{row.assignee}</span>
      ) : (
        <span className="text-text-mute">не взят</span>
      ),
  },
  dispatcher: {
    key: 'dispatcher',
    header: 'Решение диспетчера',
    width: 190,
    cell: (row, meta) => <DispatcherVerdict row={row} meta={meta} />,
  },
  outcome: {
    key: 'outcome',
    header: 'Результат бригады',
    width: 150,
    cell: (row) => {
      if (row.factConfirmed === undefined) return <span className="text-text-mute">{DASH}</span>
      return (
        <span className={row.factConfirmed ? 'text-risk-low' : 'text-text-dim'}>
          {row.factConfirmed ? 'факт подтверждён' : 'факта нет'}
        </span>
      )
    },
  },
  order: {
    key: 'order',
    header: 'Заявка',
    width: 96,
    cell: (row) => (row.orderId ? <OrderLink id={row.orderId} /> : <span className="text-text-mute">{DASH}</span>),
  },
}

/**
 * Решение диспетчера в одной ячейке: согласился он с уровнем или исправил его,
 * и кто именно решал.
 *
 * Исправление показано стрелкой «было → стало»: это и есть ярлык, на котором
 * дообучается модель (ADR 0006), и диспетчеру полезно видеть, где коллеги
 * систематически правят уровень.
 */
function DispatcherVerdict({ row, meta }: { row: Prediction; meta: AppMeta }) {
  if (!row.verdict) return <span className="text-text-mute">{DASH}</span>

  const corrected = row.verdict === 'CORRECTED' && row.dispatcherLevel
  return (
    <span className="flex flex-col gap-0.5 leading-tight">
      <span className={corrected ? 'text-risk-high' : 'text-text-dim'}>
        {corrected ? (
          <>
            {levelLabel(row.level, meta)} <span aria-hidden="true">→</span>{' '}
            {levelLabel(row.dispatcherLevel as string, meta)}
          </>
        ) : (
          'уровень подтверждён'
        )}
      </span>
    </span>
  )
}

export const orderColumns: Record<string, ColumnDef<WorkOrder>> = {
  number: {
    key: 'number',
    header: 'Номер',
    width: 104,
    sortable: true,
    cell: (row) => <Mono>{row.number}</Mono>,
  },
  facility: {
    key: 'facility',
    header: 'Объект',
    width: 240,
    sortable: true,
    cell: (row) => <FacilityCell value={row.facility} />,
  },
  workType: {
    key: 'workType',
    header: 'Вид работ',
    sortable: true,
    cell: (row) => <span className="text-text-dim">{row.workType}</span>,
  },
  dueAt: {
    key: 'dueAt',
    header: 'Срок',
    width: 190,
    sortable: true,
    /*
     * У завершённой заявки срок — просто факт, а не долг: «просрочено на 3 дня»
     * под выполненной работой сбивает с толку и раздувает список красного.
     * Конечность статуса берётся из меты, а не из сравнения с кодом.
     */
    cell: (row, meta) => {
      const done = isTerminalStatus(row.status, 'order', meta)
      const late = !done && isOverdue(row.dueAt)
      return (
        <span className="flex flex-col leading-tight">
          <Mono className={late ? 'text-risk-high' : 'text-text-dim'}>{fmtDateTime(row.dueAt)}</Mono>
          {done ? null : (
            <span className={late ? 'text-[12px] text-risk-high' : 'text-[12px] text-text-mute'}>
              {fmtDueIn(row.dueAt)}
            </span>
          )}
        </span>
      )
    },
  },
  orderStatus: {
    key: 'orderStatus',
    header: 'Статус',
    width: 180,
    sortable: true,
    cell: (row, meta) => (
      <Badge color={statusColor(row.status, 'order', meta)}>
        {statusLabel(row.status, 'order', meta)}
      </Badge>
    ),
  },
  prediction: {
    key: 'prediction',
    header: 'Прогноз',
    width: 100,
    cell: (row) => (
      <Link
        to={`/journal?prediction=${encodeURIComponent(row.predictionId)}`}
        onClick={(event) => event.stopPropagation()}
        className="text-text-dim underline decoration-line-strong underline-offset-2 hover:text-text"
      >
        <Mono>{row.predictionId}</Mono>
      </Link>
    ),
  },
}

/**
 * Разворачивает список ключей из меты в колонки.
 * Неизвестные ключи отбрасываются — экран остаётся рабочим.
 */
export function resolveColumns<Row>(
  keys: string[],
  registry: Record<string, ColumnDef<Row>>,
): ColumnDef<Row>[] {
  return keys.map((key) => registry[key]).filter((column): column is ColumnDef<Row> => Boolean(column))
}

/** Колонки для выгрузки в CSV: те же ключи, но плоские значения. */
export function csvColumnsFor(keys: string[], meta: AppMeta) {
  const map: Record<string, { header: string; value: (row: Prediction) => unknown }> = {
    computedAt: { header: 'Рассчитан', value: (r) => fmtDateTime(r.computedAt) },
    direction: { header: 'Направление', value: (r) => directionLabel(r.direction, meta) },
    risk: { header: 'Уровень', value: (r) => r.level },
    facility: { header: 'Объект', value: (r) => `${r.facility.address} (${fmtFacilityPath(r.facility)})` },
    summary: { header: 'Прогноз', value: (r) => r.summary },
    probability: { header: 'Вероятность', value: (r) => r.probability },
    horizon: { header: 'Горизонт, ч', value: (r) => r.horizonHours },
    status: { header: 'Статус', value: (r) => statusLabel(r.status, 'prediction', meta) },
    assignee: { header: 'Исполнитель', value: (r) => r.assignee ?? '' },
    dispatcher: {
      header: 'Действие диспетчера',
      value: (r) => {
        if (!r.verdict) return ''
        if (r.verdict === 'AGREED') return 'уровень подтверждён'
        return `исправлен на ${levelLabel(r.dispatcherLevel ?? '', meta)}`
      },
    },
    outcome: {
      header: 'Результат бригады',
      value: (r) => {
        if (r.factConfirmed === undefined) return ''
        return r.factConfirmed ? 'факт подтверждён' : 'факта нет'
      },
    },
    order: { header: 'Заявка', value: (r) => r.orderId ?? '' },
  }
  return keys
    .map((key) => (map[key] ? { key, ...map[key] } : undefined))
    .filter((c): c is { key: string; header: string; value: (row: Prediction) => unknown } => Boolean(c))
}
