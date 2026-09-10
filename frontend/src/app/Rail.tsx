/**
 * Рельс разделов: иконка и подпись под ней, состав из флагов экранов.
 *
 * Подпись видна всегда. Голые иконки диспетчеры читали неправильно: значение
 * раскрывалось только по наведению, а на дежурстве никто не водит мышью по
 * панели, чтобы выяснить, куда ведёт кнопка. Ширины 72 px хватает на короткое
 * слово, и рельс остаётся рельсом, а не боковым меню.
 *
 * Подписи короче пунктов навигации: «Дашборд» вместо «Дашборд рисков». Полное
 * название остаётся в `title` и в доступном имени.
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
    <div className="flex w-[72px] shrink-0 flex-col border-r border-line bg-panel py-2">
      <nav aria-label="Разделы" className="flex flex-col gap-0.5">
        {enabledNavItems().map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            title={item.label}
            aria-label={item.label}
            className={({ isActive }) =>
              cn(
                'relative mx-1.5 flex flex-col items-center gap-1 rounded px-1 py-2',
                'outline-offset-[-2px] transition-colors duration-150',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-line-strong',
                // Активный раздел кодируется дважды: заливкой и планкой слева.
                'before:absolute before:top-1.5 before:bottom-1.5 before:left-0 before:w-[3px]',
                'before:rounded-[1px] before:transition-colors before:duration-150',
                isActive
                  ? 'bg-raised text-text before:bg-accent-strong'
                  : 'text-text-mute before:bg-transparent hover:bg-raised hover:text-text-dim',
              )
            }
          >
            <Icon name={item.icon} size={18} />
            <span className="text-[11px] leading-none">{item.short}</span>
          </NavLink>
        ))}
      </nav>

      <ThemeToggle />
    </div>
  )
}
