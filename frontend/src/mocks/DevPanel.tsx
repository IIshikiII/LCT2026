/**
 * Дев-панель заглушек. Монтируется отдельным корнем в src/main.tsx только при
 * VITE_USE_MOCKS=true, поэтому код приложения о ней ничего не знает.
 *
 * Нужна на демонстрации: тумблеры позволяют за секунду показать жюри, что
 * приложение переживает поломанную /meta и что пятое направление появляется без
 * правок кода (docs/04-mocks.md).
 */
import { useState } from 'react'
import { devFlags, type DevFlags } from './devFlags'
import { resetMockDb } from './handlers'

const TOGGLES: { key: keyof DevFlags; label: string; hint: string }[] = [
  {
    key: 'extraDirection',
    label: 'Пятое направление',
    hint: 'Добавляет направление в /meta и в данные. Правок кода не требуется.',
  },
  {
    key: 'breakMeta',
    label: 'Сломать /meta',
    hint: 'Ответ 500. Приложение должно подняться на запасных конфигах.',
  },
  { key: 'failLists', label: 'Ошибки списков', hint: 'Списочные ручки отвечают 500.' },
  { key: 'slowNetwork', label: 'Медленная сеть', hint: 'Задержка ответов 2–4 секунды.' },
]

export function DevPanel() {
  const [open, setOpen] = useState(false)
  const [, force] = useState(0)
  const flags = devFlags.all()

  const toggle = (key: keyof DevFlags) => {
    devFlags.set(key, !flags[key])
    resetMockDb()
    force((n) => n + 1)
    // Перерисовать всё приложение проще, чем инвалидировать кэш снаружи React.
    window.dispatchEvent(new Event('mock-flags-changed'))
  }

  const active = TOGGLES.filter((t) => flags[t.key]).length

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Дев-панель заглушек"
        className="fixed right-3 bottom-3 z-50 rounded border border-line-strong bg-panel px-2.5 py-1.5 text-[12px] text-text-dim hover:text-text"
      >
        моки{active > 0 ? ` · ${active}` : ''}
      </button>
    )
  }

  return (
    <aside className="fixed right-3 bottom-3 z-50 w-72 rounded border border-line-strong bg-panel p-3 text-[12px]">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium text-text">Дев-панель заглушек</span>
        <button type="button" onClick={() => setOpen(false)} className="text-text-mute hover:text-text">
          свернуть
        </button>
      </div>

      <ul className="flex flex-col gap-2">
        {TOGGLES.map((item) => (
          <li key={item.key}>
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={flags[item.key]}
                onChange={() => toggle(item.key)}
                className="mt-0.5 size-3.5 accent-[var(--accent-strong)]"
              />
              <span>
                <span className="text-text-dim">{item.label}</span>
                <span className="block text-text-mute">{item.hint}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={() => {
          devFlags.reset()
          resetMockDb()
          window.location.reload()
        }}
        className="mt-3 w-full rounded border border-line bg-sunken py-1 text-text-dim hover:text-text"
      >
        Сбросить всё и перезагрузить
      </button>
    </aside>
  )
}
