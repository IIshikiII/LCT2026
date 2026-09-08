/**
 * Рельс разделов: четыре иконки, состав из флагов экранов.
 *
 * Иконка без подписи допустима здесь и только здесь — у каждой есть title и
 * доступное имя (docs/05-ui-kit.md).
 */
import { NavLink } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { Icon } from '@/shared/ui/Icon'
import { enabledNavItems } from './router'

export function Rail() {
  return (
    <nav aria-label="Разделы" className="flex w-12 shrink-0 flex-col gap-1 border-r border-line bg-panel py-2">
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
              isActive ? 'bg-raised text-text' : 'text-text-mute hover:bg-raised hover:text-text-dim',
            )
          }
        >
          <Icon name={item.icon} size={18} />
        </NavLink>
      ))}
    </nav>
  )
}
