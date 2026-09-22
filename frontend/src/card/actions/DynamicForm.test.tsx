/**
 * Критерий приёмки (spec §12): действие с неизвестным фронту кодом рисуется
 * обычной кнопкой с формой, собранной по описанию полей.
 *
 * Плюс граница строгости: на запись валидация жёсткая, в отличие от разбора
 * ответов (ADR 0006).
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ActionDef, AppMeta } from '@/shared/api/types'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { ActionBar } from './ActionBar'
import { buildZodSchema } from './buildZodSchema'

const meta: AppMeta = {
  ...FALLBACK_META,
  reasons: {
    причины: [
      { code: 'A', label: 'Первая причина' },
      { code: 'B', label: 'Вторая причина' },
    ],
  },
}

/** Действие, про которое фронт ничего не знает: кода нет в actionRegistry. */
const unknownAction: ActionDef = {
  code: 'совершенно-новое-действие',
  label: 'Сделать неизвестное',
  kind: 'primary',
  fields: [
    { name: 'reason', label: 'Причина', type: 'select', required: true, optionsRef: 'причины' },
    { name: 'comment', label: 'Комментарий', type: 'textarea', required: true, minLength: 5 },
    { name: 'exotic', label: 'Поле неизвестного типа', type: 'квантовое' },
  ],
}

function renderBar(action: ActionDef, onRun = vi.fn().mockResolvedValue(undefined)) {
  render(<ActionBar actions={[action]} meta={meta} onRun={onRun} />)
  return onRun
}

/**
 * Кнопка в панели действий и кнопка отправки формы называются одинаково —
 * это нормально для пользователя, но неоднозначно для запроса. Отправку ищем
 * внутри формы, у которой есть доступное имя.
 */
function submitButton(label: string) {
  return within(screen.getByRole('form', { name: label })).getByRole('button', { name: label })
}

describe('buildZodSchema', () => {
  it('делает обязательное текстовое поле непустым', () => {
    const schema = buildZodSchema([{ name: 'a', label: 'A', type: 'text', required: true }])
    expect(schema.safeParse({ a: '' }).success).toBe(false)
    expect(schema.safeParse({ a: 'x' }).success).toBe(true)
  })

  it('учитывает minLength', () => {
    const schema = buildZodSchema([
      { name: 'a', label: 'A', type: 'textarea', required: true, minLength: 5 },
    ])
    expect(schema.safeParse({ a: 'abc' }).success).toBe(false)
    expect(schema.safeParse({ a: 'abcde' }).success).toBe(true)
  })

  it('пропускает необязательное поле пустым', () => {
    const schema = buildZodSchema([{ name: 'a', label: 'A', type: 'text' }])
    expect(schema.safeParse({ a: '' }).success).toBe(true)
  })

  it('обрабатывает поле неизвестного типа как текстовое', () => {
    const schema = buildZodSchema([
      { name: 'a', label: 'A', type: 'нечто-новое', required: true },
    ])
    expect(schema.safeParse({ a: '' }).success).toBe(false)
    expect(schema.safeParse({ a: 'значение' }).success).toBe(true)
  })
})

describe('ActionBar с неизвестным действием', () => {
  it('рисует кнопку с подписью из API', () => {
    renderBar(unknownAction)
    expect(screen.getByRole('button', { name: 'Сделать неизвестное' })).toBeInTheDocument()
  })

  it('раскрывает форму по всем полям описания', async () => {
    const user = userEvent.setup()
    renderBar(unknownAction)
    await user.click(screen.getByRole('button', { name: 'Сделать неизвестное' }))

    expect(screen.getByLabelText(/Причина/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Комментарий/)).toBeInTheDocument()
    // Неизвестный тип поля не потерялся — он стал текстовым вводом.
    expect(screen.getByLabelText(/Поле неизвестного типа/)).toBeInTheDocument()
  })

  it('берёт опции select из справочника меты', async () => {
    const user = userEvent.setup()
    renderBar(unknownAction)
    await user.click(screen.getByRole('button', { name: 'Сделать неизвестное' }))

    expect(screen.getByRole('option', { name: 'Первая причина' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Вторая причина' })).toBeInTheDocument()
  })

  it('не отправляет форму с незаполненными обязательными полями', async () => {
    const user = userEvent.setup()
    const onRun = renderBar(unknownAction)

    await user.click(screen.getByRole('button', { name: 'Сделать неизвестное' }))
    await user.click(submitButton('Сделать неизвестное'))

    expect(onRun).not.toHaveBeenCalled()
    expect(await screen.findAllByRole('alert')).not.toHaveLength(0)
  })

  it('отправляет значения в единый эндпоинт действий', async () => {
    const user = userEvent.setup()
    const onRun = renderBar(unknownAction)

    await user.click(screen.getByRole('button', { name: 'Сделать неизвестное' }))
    await user.selectOptions(screen.getByLabelText(/Причина/), 'B')
    await user.type(screen.getByLabelText(/Комментарий/), 'проверено на месте')
    await user.click(submitButton('Сделать неизвестное'))

    expect(onRun).toHaveBeenCalledWith(
      'совершенно-новое-действие',
      expect.objectContaining({ reason: 'B', comment: 'проверено на месте' }),
    )
  })

  it('сообщает, когда действий нет', () => {
    render(<ActionBar actions={[]} meta={meta} onRun={vi.fn()} />)
    expect(screen.getByText(/Доступных действий нет/)).toBeInTheDocument()
  })
})

describe('ActionBar с действием, у которого своя форма', () => {
  const closeAction: ActionDef = {
    code: 'close',
    label: 'Закрыть заявку',
    kind: 'primary',
    fields: [
      { name: 'actualCause', label: 'Фактическая причина', type: 'select', required: true, optionsRef: 'причины' },
      { name: 'factConfirmed', label: 'Факт подтверждён на объекте', type: 'boolean', required: true },
      { name: 'comment', label: 'Что сделано', type: 'textarea', required: true, minLength: 5 },
    ],
  }

  it('показывает отметку подтверждения радиовыбором, а не галочкой', async () => {
    const user = userEvent.setup()
    renderBar(closeAction)
    await user.click(screen.getByRole('button', { name: 'Закрыть заявку' }))

    expect(screen.getByRole('radio', { name: /событие подтвердилось/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /тревога ложная/i })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('не даёт закрыть заявку без отметки — из неё считается качество модели', async () => {
    const user = userEvent.setup()
    const onRun = renderBar(closeAction)

    await user.click(screen.getByRole('button', { name: 'Закрыть заявку' }))
    await user.selectOptions(screen.getByLabelText(/Фактическая причина/), 'A')
    await user.type(screen.getByLabelText(/Что сделано/), 'дефект устранён')
    await user.click(submitButton('Закрыть заявку'))

    expect(onRun).not.toHaveBeenCalled()
    expect(screen.getByText(/Отметьте, подтверждён ли факт/)).toBeInTheDocument()
  })

  it('передаёт отметку булевым значением', async () => {
    const user = userEvent.setup()
    const onRun = renderBar(closeAction)

    await user.click(screen.getByRole('button', { name: 'Закрыть заявку' }))
    await user.selectOptions(screen.getByLabelText(/Фактическая причина/), 'A')
    await user.click(screen.getByRole('radio', { name: /тревога ложная/i }))
    await user.type(screen.getByLabelText(/Что сделано/), 'дефект не подтверждён')
    await user.click(submitButton('Закрыть заявку'))

    expect(onRun).toHaveBeenCalledWith('close', {
      actualCause: 'A',
      factConfirmed: false,
      comment: 'дефект не подтверждён',
    })
  })
})
