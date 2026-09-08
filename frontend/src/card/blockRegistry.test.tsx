/**
 * Критерии приёмки (spec §12), которые держит этот файл:
 *  - блок неизвестного типа рисуется запасным рендерером, экран не падает;
 *  - блок с битыми данными деградирует, а не роняет карточку;
 *  - блок, бросивший исключение, заменяется сообщением, соседние блоки живы.
 */
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CardBlock } from '@/shared/api/types'
import { BlockList, BlockRenderer, blockRegistry } from './blockRegistry'

describe('BlockRenderer', () => {
  it('рисует известный тип штатным компонентом', () => {
    const block: CardBlock = {
      type: 'keyvalue',
      title: 'Паспорт объекта',
      data: { items: [{ label: 'Наработка', value: 1200 }] },
    }
    render(<BlockRenderer block={block} />)
    expect(screen.getByText('Паспорт объекта')).toBeInTheDocument()
    expect(screen.getByText('Наработка')).toBeInTheDocument()
  })

  it('рисует неизвестный тип запасным рендерером, а не пустотой', () => {
    const block: CardBlock = {
      type: 'что-то-совершенно-новое',
      title: 'Экспериментальный признак',
      data: { источник: 'новая ветка конвейера', вклад: 0.42 },
    }
    render(<BlockRenderer block={block} />)
    expect(screen.getByText('Экспериментальный признак')).toBeInTheDocument()
    expect(screen.getByText('источник')).toBeInTheDocument()
    expect(screen.getByText('новая ветка конвейера')).toBeInTheDocument()
  })

  it('деградирует до запасного рендерера, если форма данных не совпала', () => {
    const block: CardBlock = {
      type: 'factors',
      title: 'Вклад признаков',
      data: { items: 'здесь должен был быть массив' },
    }
    render(<BlockRenderer block={block} />)
    // Заголовок на месте, содержимое показано как есть — карточка цела.
    expect(screen.getByText('Вклад признаков')).toBeInTheDocument()
    expect(screen.getByText('здесь должен был быть массив')).toBeInTheDocument()
  })

  it('переживает совсем пустые данные', () => {
    for (const data of [null, undefined, 0, '', []]) {
      const { unmount } = render(
        <BlockRenderer block={{ type: 'unknown', title: 'Пусто', data }} />,
      )
      expect(screen.getByText('Пусто')).toBeInTheDocument()
      unmount()
    }
  })
})

describe('ErrorBoundary вокруг блока', () => {
  const Broken = () => {
    throw new Error('блок сломался')
  }

  beforeEach(() => {
    blockRegistry['broken'] = Broken
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    delete blockRegistry['broken']
    vi.restoreAllMocks()
  })

  it('заменяет упавший блок сообщением и не трогает соседние', () => {
    const blocks: CardBlock[] = [
      { type: 'keyvalue', title: 'До', data: { items: [{ label: 'а', value: 1 }] } },
      { type: 'broken', title: 'Сломанный блок', data: {} },
      { type: 'keyvalue', title: 'После', data: { items: [{ label: 'б', value: 2 }] } },
    ]
    render(<BlockList blocks={blocks} />)

    expect(screen.getByText('До')).toBeInTheDocument()
    expect(screen.getByText('После')).toBeInTheDocument()
    expect(screen.getByText('Сломанный блок')).toBeInTheDocument()
    expect(screen.getByText(/Блок не удалось отобразить/)).toBeInTheDocument()
  })
})

describe('BlockList', () => {
  it('сообщает, когда объяснение не пришло', () => {
    render(<BlockList blocks={[]} />)
    expect(screen.getByText(/Объяснение прогноза не пришло/)).toBeInTheDocument()
  })
})
