/**
 * Переключение темы (ADR 0009).
 *
 * Цвета здесь не проверяются: jsdom не считает каскад, и утверждение «фон стал
 * светлым» было бы фикцией. Проверяется механика — атрибут на <html>, его
 * сохранение и то, что все потребители узнают о смене без глобального стора.
 * Соответствие палитр лежит на src/styles/tokens.test.ts.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_THEME, THEME_STORAGE_KEY, applyTheme, currentTheme, useTheme } from './theme'

function Probe({ label }: { label: string }) {
  const { theme, toggle } = useTheme()
  return (
    <button type="button" onClick={toggle}>
      {label}: {theme}
    </button>
  )
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('тема интерфейса', () => {
  it('по умолчанию тёмная — основной режим продукта', () => {
    expect(currentTheme()).toBe(DEFAULT_THEME)
    expect(DEFAULT_THEME).toBe('dark')
  })

  it('читает тему, уже проставленную скриптом из index.html', () => {
    document.documentElement.dataset['theme'] = 'light'
    expect(currentTheme()).toBe('light')
  })

  it('игнорирует мусор в хранилище и откатывается к тёмной', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'неоновая')
    expect(currentTheme()).toBe('dark')
  })

  it('нажатие переключает тему и запоминает выбор', async () => {
    const user = userEvent.setup()
    render(<Probe label="рельс" />)

    expect(screen.getByRole('button')).toHaveTextContent('рельс: dark')

    await user.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(document.documentElement.dataset['theme']).toBe('light')
    })
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(screen.getByRole('button')).toHaveTextContent('рельс: light')
  })

  it('возвращает обратно повторным нажатием', async () => {
    const user = userEvent.setup()
    render(<Probe label="рельс" />)

    await user.click(screen.getByRole('button'))
    await waitFor(() => expect(currentTheme()).toBe('light'))
    await user.click(screen.getByRole('button'))

    await waitFor(() => expect(currentTheme()).toBe('dark'))
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
  })

  it('о смене узнают все потребители, а не только тот, кто переключил', async () => {
    const user = userEvent.setup()
    render(
      <>
        <Probe label="рельс" />
        <Probe label="карта" />
      </>,
    )

    await user.click(screen.getByText(/рельс: dark/))

    // Карта не получала пропсов и не подписана на рельс: обе кнопки следят
    // за самим атрибутом. Именно на этом держится перекраска слоёв MapLibre.
    await waitFor(() => {
      expect(screen.getByText(/карта: light/)).toBeInTheDocument()
    })
  })

  it('переживает недоступный localStorage — приватный режим не роняет интерфейс', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('доступ к хранилищу запрещён')
    })

    expect(() => applyTheme('light')).not.toThrow()
    expect(document.documentElement.dataset['theme']).toBe('light')

    setItem.mockRestore()
  })
})
