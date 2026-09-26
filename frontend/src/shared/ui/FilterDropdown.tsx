/**
 * Выпадающий фильтр с флажками.
 *
 * Панель фильтров держала двенадцать плашек подряд: три подсистемы, четыре
 * уровня риска, пять статусов. Ширины экрана хватало, внимания диспетчера нет.
 *
 * Плашками остался только уровень риска: он читается цветом, а не текстом, и
 * это главный признак строки в журнале. Остальное свёрнуто в кнопки, которые
 * занимают одну позицию каждая независимо от числа вариантов.
 *
 * Раскрытие сделано на `details`, а не на своём поповере. Браузер сам даёт
 * клавиатуру, `Esc` и правильную семантику, а нам остаётся оформление.
 */
import { useEffect, useRef } from 'react'
import { cn } from '@/shared/lib/cn'

export interface FilterOption {
  code: string
  label: string
  /** Точка слева: акцент подсистемы или цвет статуса. */
  color?: string
}

export interface FilterDropdownProps {
  /** Подпись кнопки, когда ничего не выбрано. */
  title: string
  options: FilterOption[]
  selected: string[]
  onToggle: (code: string) => void
  /** Снять все флажки этой группы. Кнопка появляется только при выборе. */
  onClear?: () => void
}

export function FilterDropdown({
  title,
  options,
  selected,
  onToggle,
  onClear,
}: FilterDropdownProps) {
  const ref = useRef<HTMLDetailsElement>(null)

  // Клик мимо закрывает список. Без этого открытыми остаются сразу несколько,
  // и панель разрастается ровно так, как мы её и сворачивали.
  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      const element = ref.current
      if (element?.open && !element.contains(event.target as Node)) {
        element.open = false
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [])

  const count = selected.length

  return (
    <details ref={ref} className="relative">
      <summary
        className={cn(
          'flex h-6 cursor-pointer list-none items-center gap-1.5 rounded border px-2 text-[12px]',
          'transition-colors duration-150 outline-offset-2 select-none',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
          '[&::-webkit-details-marker]:hidden',
          count > 0
            ? 'border-line-strong bg-raised text-text'
            : 'border-line bg-transparent text-text-dim hover:text-text',
        )}
      >
        {title}
        {count > 0 ? <span className="mono text-text-mute">{count}</span> : null}
        <span aria-hidden="true" className="text-[9px] text-text-mute">
          ▼
        </span>
      </summary>

      <div
        className={cn(
          'absolute top-7 left-0 z-20 flex min-w-52 flex-col gap-0.5 rounded',
          'border border-line bg-panel p-1 shadow-lg',
        )}
      >
        {options.map((option) => (
          <label
            key={option.code}
            className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-[12px] text-text-dim hover:bg-raised hover:text-text"
          >
            <input
              type="checkbox"
              className="size-3 accent-[var(--accent)]"
              checked={selected.includes(option.code)}
              onChange={() => onToggle(option.code)}
            />
            {option.color ? (
              <span
                aria-hidden="true"
                className="size-1.5 shrink-0 rounded-full"
                style={{ background: option.color }}
              />
            ) : null}
            <span className="truncate">{option.label}</span>
          </label>
        ))}

        {count > 0 && onClear ? (
          <button
            type="button"
            onClick={onClear}
            className="mt-0.5 border-t border-line px-1.5 pt-1.5 text-left text-[11px] text-text-mute hover:text-text"
          >
            Снять выбор
          </button>
        ) : null}
      </div>
    </details>
  )
}
