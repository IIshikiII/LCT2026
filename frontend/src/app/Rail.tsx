/**
 * Рельс разделов: четыре иконки, состав из флагов экранов.
 *
 * Иконка без подписи допустима здесь и только здесь — у каждой есть title и
 * доступное имя (docs/05-ui-kit.md).
 *
 * Внизу, отбитый от разделов, — переключатель темы (ADR 0009). Это
 * единственная кнопка рельса, которая не ведёт на экран, поэтому она вынесена
 * из <nav> в отдельный блок: в списке разделов ей не место.
 */
import { NavLink } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { useTheme } from '@/shared/lib/theme'
import { Icon } from '@/shared/ui/Icon'
import { enabledNavItems } from './router'

/** Подпись говорит, что произойдёт по нажатию, а не что включено сейчас. */
function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const label = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'

  return (
    <button
      type="button"
      onClick={toggle}
      title={label}
      aria-label={label}
      className={cn(
        'mx-1.5 mt-auto mb-1 flex h-9 items-center justify-center rounded outline-offset-[-2px]',
        'text-text-mute transition-colors duration-150',
        'hover:bg-raised hover:text-text-dim',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
      )}
    >
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={18} />
    </button>
  )
}

export function Rail() {
  return (
    <div className="flex w-12 shrink-0 flex-col border-r border-line bg-panel py-2">
      <nav aria-label="Разделы" className="flex flex-col gap-1">
        {enabledNavItems().map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            title={item.label}
            aria-label={item.label}
            className={({ isActive }) =>
              cn(
                'mx-1.5 flex h-9 items-center justify-center rounded outline-offset-[-2px]',
                'transition-colors duration-150',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
                isActive
                  ? 'bg-raised text-text'
                  : 'text-text-mute hover:bg-raised hover:text-text-dim',
              )
            }
          >
            <Icon name={item.icon} size={18} />
          </NavLink>
        ))}
      </nav>

      <ThemeToggle />
    </div>
  )
}
