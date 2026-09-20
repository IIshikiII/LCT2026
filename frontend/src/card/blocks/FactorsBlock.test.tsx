/**
 * Критерии приёмки (ADR 0011), которые держит этот файл:
 *  - положительный вклад рисуется вправо от нуля, отрицательный — влево;
 *  - пустой список факторов не роняет блок;
 *  - вес вне диапазона плюс-минус 1 прижимается к границе, а не растягивает ось;
 *  - список длиннее шести строк сворачивается за кнопку и разворачивается по клику;
 *  - необязательное поле note может отсутствовать.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { CardBlock } from '@/shared/api/types'
import { FactorsBlock } from './FactorsBlock'

function block(data: unknown): CardBlock {
  return { type: 'factors', title: 'Вклад признаков', data }
}

describe('FactorsBlock', () => {
  it('показывает положительный вклад как повышающий риск', () => {
    render(
      <FactorsBlock
        block={block({ items: [{ label: 'Ночная доля', weight: 0.6, value: '0,6' }] })}
      />,
    )
    expect(screen.getByText('Ночная доля')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Ночная доля: повышает риск/ })).toBeInTheDocument()
  })

  it('показывает отрицательный вклад как снижающий риск', () => {
    render(
      <FactorsBlock
        block={block({ items: [{ label: 'Дневное время', weight: -0.4 }] })}
      />,
    )
    expect(screen.getByRole('img', { name: /Дневное время: снижает риск/ })).toBeInTheDocument()
  })

  it('переживает пустой список факторов', () => {
    render(<FactorsBlock block={block({ items: [] })} />)
    expect(screen.getByText('Вклад признаков')).toBeInTheDocument()
    expect(screen.queryAllByRole('img')).toHaveLength(0)
  })

  it('прижимает вес вне диапазона к границе плюс-минус 1', () => {
    render(<FactorsBlock block={block({ items: [{ label: 'Аномалия', weight: 3.5 }] })} />)
    const bar = screen.getByRole('img', { name: /Аномалия: повышает риск/ })
    const fill = bar.querySelector('span[aria-hidden="true"]:last-child') as HTMLElement
    expect(fill.style.width).toBe('50%')
  })

  it('сворачивает список длиннее шести строк и разворачивает по клику', async () => {
    const items = Array.from({ length: 9 }, (_, i) => ({ label: `Признак ${i + 1}`, weight: 0.1 }))
    render(<FactorsBlock block={block({ items })} />)

    expect(screen.getByText('Ещё 3 фактора')).toBeInTheDocument()
    expect(screen.queryByText('Признак 9')).not.toBeInTheDocument()

    await userEvent.click(screen.getByText('Ещё 3 фактора'))

    expect(screen.getByText('Признак 9')).toBeInTheDocument()
    expect(screen.queryByText(/Ещё \d+ фактор/)).not.toBeInTheDocument()
  })

  it('рисует блок без необязательного поля note', () => {
    render(<FactorsBlock block={block({ items: [{ label: 'Признак', weight: 0.2 }] })} />)
    expect(screen.getByText('Признак')).toBeInTheDocument()
  })
})
