/**
 * Панель действий карточки.
 *
 * Главное правило, которое держит этот файл: **действие без полей выполняется
 * с одного нажатия.** «Взять в работу» ничего не спрашивает, и пустая форма с
 * кнопками «Взять в работу» и «Отмена» под ней требовала двух нажатий там, где
 * хватает одного.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import type { ActionDef } from '@/shared/api/types'
import { ActionBar } from './ActionBar'

const TAKE: ActionDef = { code: 'take', label: 'Взять в работу', kind: 'primary', fields: [] }

const DECIDE: ActionDef = {
  code: 'decide',
  label: 'Принять решение',
  kind: 'primary',
  fields: [{ name: 'comment', label: 'Комментарий', type: 'textarea', required: true }],
}

const REJECT: ActionDef = {
  code: 'reject',
  label: 'Отклонить заявку',
  kind: 'danger',
  confirm: 'Отклонить без возможности вернуть?',
  fields: [],
}

function renderBar(actions: ActionDef[], onRun = vi.fn().mockResolvedValue({})) {
  render(<ActionBar actions={actions} meta={FALLBACK_META} onRun={onRun} />)
  return onRun
}

describe('панель действий', () => {
  it('выполняет действие без полей с одного нажатия', async () => {
    const user = userEvent.setup()
    const onRun = renderBar([TAKE])

    await user.click(screen.getByRole('button', { name: 'Взять в работу' }))

    expect(onRun).toHaveBeenCalledWith('take', {})
    expect(await screen.findByText(/Выполнено: взять в работу/)).toBeInTheDocument()
  })

  it('не раскрывает пустую форму под таким действием', async () => {
    const user = userEvent.setup()
    renderBar([TAKE])

    await user.click(screen.getByRole('button', { name: 'Взять в работу' }))

    // Второй кнопки с тем же именем быть не должно: форма не раскрылась.
    expect(screen.getAllByRole('button', { name: 'Взять в работу' })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Отмена' })).not.toBeInTheDocument()
  })

  it('раскрывает форму, когда есть что заполнять', async () => {
    const user = userEvent.setup()
    const onRun = renderBar([DECIDE])

    await user.click(screen.getByRole('button', { name: 'Принять решение' }))

    expect(screen.getByLabelText(/Комментарий/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeInTheDocument()
    expect(onRun).not.toHaveBeenCalled()
  })

  it('оставляет форму действию с подтверждением, даже если полей нет', async () => {
    const user = userEvent.setup()
    const onRun = renderBar([REJECT])

    await user.click(screen.getByRole('button', { name: 'Отклонить заявку' }))

    // Подтверждение и есть тот вопрос, ради которого форма нужна.
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeInTheDocument()
    expect(onRun).not.toHaveBeenCalled()
  })

  it('говорит, что действий нет, когда их нет', () => {
    renderBar([])
    expect(screen.getByText(/Доступных действий нет/)).toBeInTheDocument()
  })

  it('называет причину, которую знает вызывающий', () => {
    render(
      <ActionBar
        actions={[]}
        meta={FALLBACK_META}
        onRun={vi.fn()}
        emptyText="Работа идёт по заявке WO-1."
      />,
    )
    expect(screen.getByText('Работа идёт по заявке WO-1.')).toBeInTheDocument()
  })
})
