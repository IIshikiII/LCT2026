/**
 * Список объектов одного кружка на карте.
 *
 * Кружок на карте прячет от диспетчера ровно то, ради чего он на карту
 * смотрит: какие именно объекты стоят в этом месте и что с ними. Раньше
 * добраться до них можно было только приближением до распада кружка. Панель
 * отдаёт тот же список сразу, не трогая масштаб.
 *
 * Панель накрывает карту, а не раздвигает её: карта остаётся видна, и выбор из
 * списка сразу показан на ней подсветкой и наездом камеры.
 *
 * Карточку прогноза панель не заменяет. Нажатие на строку открывает ту же
 * правую панель, что и нажатие на точку, а список остаётся на месте — так
 * соседние объекты кружка перебираются один за другим.
 */
import type { AppMeta, FacilityFeature } from '@/shared/api/types'
import { cn } from '@/shared/lib/cn'
import { directionLabel, levelColor, levelLabel } from '@/shared/lib/risk'
import { Button } from '@/shared/ui/Button'
import { Icon } from '@/shared/ui/Icon'

export interface ClusterPanelProps {
  items: FacilityFeature[]
  meta: AppMeta
  /** Прогноз, открытый в карточке: строка списка помечается выбранной. */
  selectedId?: string
  onPick: (feature: FacilityFeature) => void
  onClose: () => void
}

export function ClusterPanel({ items, meta, selectedId, onPick, onClose }: ClusterPanelProps) {
  return (
    <aside
      aria-label="Объекты в группе"
      className={cn(
        'absolute top-2 right-2 bottom-2 z-10 flex w-[280px] flex-col',
        'rounded border border-line bg-panel',
      )}
    >
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
        <h2 className="text-[13px] font-medium text-text">
          Объектов в группе: {items.length}
        </h2>
        <Button
          size="sm"
          kind="ghost"
          onClick={onClose}
          aria-label="Закрыть список"
          title="Закрыть список"
          icon={<Icon name="close" size={14} />}
        />
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {items.map((feature) => (
          <ClusterRow
            key={feature.properties.facilityId}
            feature={feature}
            meta={meta}
            selected={
              Boolean(selectedId) && feature.properties.predictionId === selectedId
            }
            onPick={onPick}
          />
        ))}
      </ul>
    </aside>
  )
}

function ClusterRow({
  feature,
  meta,
  selected,
  onPick,
}: {
  feature: FacilityFeature
  meta: AppMeta
  selected: boolean
  onPick: (feature: FacilityFeature) => void
}) {
  const { address, facilityId, collector, direction, level, probability } = feature.properties

  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(feature)}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'flex w-full items-start gap-2 border-b border-line px-2 py-1.5 text-left',
          'hover:bg-raised focus-visible:outline focus-visible:-outline-offset-2',
          'focus-visible:outline-2 focus-visible:outline-line-strong',
          selected && 'bg-raised',
        )}
      >
        {/*
          Планка уровня. Второе кодирование риска помимо цвета текста, spec §4:
          цвет один сигнал не несёт.
        */}
        <span
          aria-hidden="true"
          className="mt-0.5 h-8 w-[3px] shrink-0 rounded-full"
          style={{ background: level ? levelColor(level, meta) : 'var(--text-mute)' }}
        />

        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-[13px] text-text">{address ?? facilityId}</span>
          <span className="truncate text-[12px] text-text-mute">
            {direction ? directionLabel(direction, meta) : facilityId}
          </span>
          <span className="truncate text-[12px] text-text-dim">
            {level ? levelLabel(level, meta) : '—'}
            {typeof probability === 'number' ? ` · ${Math.round(probability * 100)}%` : ''}
            {collector ? ` · ${collector}` : ''}
          </span>
        </span>
      </button>
    </li>
  )
}
