/**
 * Панель фильтров. Общая для журнала и карты — фильтры у них одни и те же и
 * живут в URL, поэтому синхронизация между экранами получается бесплатно
 * (docs/06-url-state.md).
 *
 * Состав фильтров целиком из меты: направления, уровни риска, статусы, районы.
 * Списка направлений в этом файле нет — он приходит из `/meta` и достраивается
 * кодами, встреченными в данных (ADR 0002).
 *
 * Фильтр по району виден только тому, кто видит всё предприятие. Остальным
 * сужать нечего: их выборку уже сузил сервер.
 *
 * **Плашек в панели нет.** Их было двенадцать в ряд, и панель читалась как
 * сплошная лента. Каждая группа свёрнута в выпадающий список и занимает одну
 * позицию независимо от числа вариантов. Цвет уровня и подсистемы не потерялся:
 * он остался точкой слева от каждой строки внутри списка.
 */
import type { ReactNode } from 'react'
import { useMeta } from '@/shared/api/queries'
import { useAuth } from '@/shared/auth/context'
import { seesWholeCompany } from '@/shared/auth/scope'
import {
  directionOptions,
  levelColor,
  levelsBySeverity,
  statusColor,
  statusOptions,
} from '@/shared/lib/risk'
import type { PredictionFilterApi } from '@/shared/lib/urlState'
import { Button } from '@/shared/ui/Button'
import { Select, TextInput } from '@/shared/ui/Field'
import { FilterDropdown } from '@/shared/ui/FilterDropdown'

export interface FilterBarProps {
  api: PredictionFilterApi
  /** Коды направлений, реально встреченные в текущей выборке. */
  seenDirections?: string[]
  /** Исполнители, встреченные в текущей выборке. Список строит экран. */
  seenAssignees?: string[]
  /** Показывать ли фильтр по статусу — на карте он не нужен. */
  withStatus?: boolean
  /** Показывать ли период — на карте он не нужен. */
  withPeriod?: boolean
  /** Кнопки справа: выгрузка, счётчики. */
  actions?: ReactNode
}

/** Подпись поля в одну строку с самим полем. */
function Labelled({ title, children }: { title: string; children: ReactNode }) {
  return (
    <label className="flex items-center gap-1.5 text-[12px] text-text-mute">
      {title}
      {children}
    </label>
  )
}

export function FilterBar({
  api,
  seenDirections = [],
  seenAssignees = [],
  withStatus = true,
  withPeriod = true,
  actions,
}: FilterBarProps) {
  const meta = useMeta()
  const { session } = useAuth()
  const { filters } = api

  // Роль, ограниченная одним районом или одним комплексом, фильтр по району не
  // получает: сузить нечего, а расширить нельзя. Границу ставит сервер
  // условием запроса, и плашка здесь только притворялась бы рычагом.
  const showDistrict = meta.districts.length > 0 && seesWholeCompany(session?.user)

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line bg-panel px-3 py-2">
      <FilterDropdown
        title="Риск"
        options={levelsBySeverity(meta).map((level) => ({
          code: level.code,
          label: level.label,
          color: levelColor(level.code, meta),
        }))}
        selected={filters.level}
        onToggle={(code) => api.toggle('level', code)}
        onClear={() => api.setList('level', [])}
      />

      <FilterDropdown
        title="Подсистема"
        options={directionOptions(meta, seenDirections).map((direction) => ({
          code: direction.code,
          label: direction.label,
          color: direction.accent,
        }))}
        selected={filters.direction}
        onToggle={(code) => api.toggle('direction', code)}
        onClear={() => api.setList('direction', [])}
      />

      {withStatus ? (
        <FilterDropdown
          title="Статус"
          options={statusOptions('prediction', meta).map((status) => ({
            code: status.code,
            label: status.label,
            color: statusColor(status.code, 'prediction', meta),
          }))}
          selected={filters.status}
          onToggle={(code) => api.toggle('status', code)}
          onClear={() => api.setList('status', [])}
        />
      ) : null}

      {seenAssignees.length > 0 ? (
        <Labelled title="Исполнитель">
          <Select
            className="h-6 w-40 py-0 text-[12px]"
            value={filters.assignee ?? ''}
            onChange={(event) => api.setValue('assignee', event.target.value || undefined)}
          >
            <option value="">все</option>
            {seenAssignees.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </Labelled>
      ) : null}

      {showDistrict ? (
        <Labelled title="Район">
          <Select
            className="h-6 w-36 py-0 text-[12px]"
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
        </Labelled>
      ) : null}

      {withPeriod ? (
        <div className="flex items-center gap-1.5 text-[12px] text-text-mute">
          Период
          <TextInput
            type="date"
            aria-label="Период с"
            className="h-6 w-32 py-0 text-[12px]"
            value={filters.from ?? ''}
            onChange={(event) => api.setValue('from', event.target.value || undefined)}
          />
          <span aria-hidden="true">—</span>
          <TextInput
            type="date"
            aria-label="Период по"
            className="h-6 w-32 py-0 text-[12px]"
            value={filters.to ?? ''}
            onChange={(event) => api.setValue('to', event.target.value || undefined)}
          />
        </div>
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
