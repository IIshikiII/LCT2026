/**
 * Тема интерфейса (ADR 0009).
 *
 * Вся тема — это значение атрибута `data-theme` на <html>; цвета меняет CSS в
 * styles/tokens.css. Здесь только выбор, его хранение и применение. Компоненты
 * не ветвятся по теме: единственное место, где вообще нужно знать активную
 * тему, — иконка и подпись переключателя в рельсе.
 *
 * Первичное применение делает встроенный скрипт в index.html, до загрузки
 * бандла: иначе светлая тема моргнёт тёмным на первом кадре. Ключ хранилища
 * и тема по умолчанию продублированы там строкой и должны совпадать с
 * THEME_STORAGE_KEY и DEFAULT_THEME.
 */
import { useCallback, useSyncExternalStore } from 'react'

export type Theme = 'dark' | 'light'

export const THEME_STORAGE_KEY = 'arm-theme'

/** Светлая — основная тема продукта, spec §4. */
export const DEFAULT_THEME: Theme = 'light'

function isTheme(value: unknown): value is Theme {
  return value === 'dark' || value === 'light'
}

/** Выбор из хранилища. Недоступный localStorage — не повод ронять интерфейс. */
export function storedTheme(): Theme | undefined {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY)
    return isTheme(raw) ? raw : undefined
  } catch {
    return undefined
  }
}

/** Тема, действующая прямо сейчас: то, что скрипт уже проставил на <html>. */
export function currentTheme(): Theme {
  const attribute = document.documentElement.dataset['theme']
  if (isTheme(attribute)) return attribute
  return storedTheme() ?? DEFAULT_THEME
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset['theme'] = theme
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Приватный режим: тема работает, но не переживёт перезагрузку.
  }
}

/**
 * Источник истины — сам атрибут на <html>, а не копия состояния в React.
 * Так все потребители синхронны без глобального стора (ADR 0001): рельс
 * переключил тему, карта тут же узнала и перекрасила слои.
 */
function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
  return () => observer.disconnect()
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => DEFAULT_THEME)

  const toggle = useCallback(() => {
    applyTheme(currentTheme() === 'dark' ? 'light' : 'dark')
  }, [])

  return { theme, toggle }
}
