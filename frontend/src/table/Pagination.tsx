/**
 * Серверная пагинация. Номера страниц не рисуем: при 260 записях достаточно
 * «назад / вперёд» и счётчика, а список из тринадцати кнопок только шумит.
 */
import { PAGE_SIZE } from '@/shared/api/filters'
import { fmtNumber } from '@/shared/lib/format'
import { Button } from '@/shared/ui/Button'
import { Icon } from '@/shared/ui/Icon'
import { Mono } from '@/shared/ui/Mono'

export interface PaginationProps {
  page: number
  total: number
  pageSize?: number
  onChange: (page: number) => void
}

export function Pagination({ page, total, pageSize = PAGE_SIZE, onChange }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)

  return (
    <div className="flex h-9 shrink-0 items-center justify-between border-t border-line px-3">
      <span className="text-[12px] text-text-mute">
        <Mono>
          {fmtNumber(first)}–{fmtNumber(last)}
        </Mono>{' '}
        из <Mono>{fmtNumber(total)}</Mono>
      </span>
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          kind="ghost"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          icon={<Icon name="chevronLeft" size={14} />}
          aria-label="Предыдущая страница"
        />
        <span className="text-[12px] text-text-mute">
          <Mono>
            {fmtNumber(page)} / {fmtNumber(pages)}
          </Mono>
        </span>
        <Button
          size="sm"
          kind="ghost"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
          icon={<Icon name="chevronRight" size={14} />}
          aria-label="Следующая страница"
        />
      </div>
    </div>
  )
}
