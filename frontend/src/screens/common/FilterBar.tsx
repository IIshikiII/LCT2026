/**
 * Панель фильтров. Общая для журнала и карты — фильтры у них одни и те же и
 * живут в URL, поэтому синхронизация между экранами получается бесплатно
 * (docs/06-url-state.md).
 *
 * Состав фильтров целиком из меты: направления, уровни риска, статусы, районы.
 * Списка направлений в этом файле нет — он приходит из `/meta` и достраивается
 * кодами, встреченными в данных (ADR 0002).
 */
import type { ReactNode } from 'react'
import { useMeta } from '@/shared/api/queries'
import { directionOptions, levelsBySeverity, statusColor, statusOptions } from '@/shared/lib/risk'
import type { PredictionFilterApi } from '@/shared/lib/urlState'
import { Button } from '@/shared/ui/Button'
import { Chip } from '@/shared/ui/Chip'
import { Select, TextInput } from '@/shared/ui/Field'
import { levelColor } from '@/shared/lib/risk'

export interface FilterBarProps {
  api: PredictionFilterApi
  /** Коды направлений, реально встреченные в текущей выборке. */
  seenDirections?: string[]
  /** Показывать ли фильтр по статусу — на карте он не нужен. */
  withStatus?: boolean
  /** Показывать ли период — на карте он не нужен. */
  withPeriod?: boolean
  /** Кнопки справа: выгрузка, счётчики. */
  actions?: ReactNode
}

export function FilterBar({
  api,
  seenDirections = [],
  withStatus = true,
  withPeriod = true,
  actions,
}: FilterBarProps) {
  const meta = useMeta()
  const { filters } = api

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel px-3 py-2">
      <fieldset className="flex flex-wrap items-center gap-1.5">
        <legend className="sr-only">Направление прогнозирования</legend>
        {directionOptions(meta, seenDirections).map((direction) => (
          <Chip
            key={direction.code}
            label={direction.shortLabel}
            title={direction.label}
            color={direction.accent}
            active={filters.direction.includes(direction.code)}
            onToggle={() => api.toggle('direction', direction.code)}
          />
        ))}
      </fieldset>

      <fieldset className="flex flex-wrap items-center gap-1.5">
        <legend className="sr-only">Уровень риска</legend>
        {levelsBySeverity(meta).map((level) => (
          <Chip
            key={level.code}
            label={level.label}
            color={levelColor(level.code, meta)}
            active={filters.level.includes(level.code)}
            onToggle={() => api.toggle('level', level.code)}
          />
        ))}
      </fieldset>

      {withStatus ? (
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Статус прогноза</legend>
          {statusOptions('prediction', meta).map((status) => (
            <Chip
              key={status.code}
              label={status.label}
              color={statusColor(status.code, 'prediction', meta)}
              active={filters.status.includes(status.code)}
              onToggle={() => api.toggle('status', status.code)}
            />
          ))}
        </fieldset>
      ) : null}

      {meta.districts.length > 0 ? (
        <label className="flex items-center gap-1.5 text-[12px] text-text-mute">
          Район
          <Select
            className="h-6 w-40 py-0 text-[12px]"
            value={filters.district ?? ''}
            onChange={(event) => api.setValue('district', event.target.value || undefined)}
          >
            <option value="">все</option>
            {meta.districts.map((district) => (
              <option key={district.code} value={district.code}>
                {district.label}
              </option>
            ))}
          </Select>
        </label>
      ) : null}

      {withPeriod ? (
        <label className="flex items-center gap-1.5 text-[12px] text-text-mute">
          С
          <TextInput
            type="date"
            className="h-6 w-36 py-0 text-[12px]"
            value={filters.from ?? ''}
            onChange={(event) => api.setValue('from', event.target.value || undefined)}
          />
        </label>
      ) : null}

      <div className="ml-auto flex items-center gap-1.5">
        {api.isActive ? (
          <Button size="sm" kind="ghost" onClick={api.reset}>
            Сбросить фильтры
          </Button>
        ) : null}
        {actions}
      </div>
    </div>
  )
}
