/**
 * Заявки и полный цикл диспетчера.
 *
 * Критерий приёмки (spec §12): цикл «прогноз → подтверждение заявки → закрытие
 * с разметкой» проходится целиком. Разметка `factConfirmed` — то, из чего
 * считаются честные Precision и Recall, поэтому её обязательность проверяется
 * отдельно (ADR 0004).
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { AppShell } from '@/app/AppShell'
import { db } from '@/mocks/db'
import { currentSearch, renderWithProviders } from '@/test/renderWithProviders'

/** Кнопка отправки формы — в панели действий есть одноимённая кнопка-раскрывашка. */
function submitIn(label: string) {
  return within(screen.getByRole('form', { name: label })).getByRole('button', { name: label })
}

describe('экран заявок', () => {
  it('рисует колонки из меты и строки из API', async () => {
    renderWithProviders(<AppShell />, { route: '/orders' })

    const table = await screen.findByRole('table')
    for (const header of ['Номер', 'Объект', 'Вид работ', 'Срок', 'Статус']) {
      expect(within(table).getByRole('columnheader', { name: new RegExp(header) })).toBeInTheDocument()
    }
  })

  it('фильтрует по статусу через URL', async () => {
    renderWithProviders(<AppShell />, { route: '/orders?orderStatus=CLOSED_CONFIRMED' })

    await screen.findByRole('table')
    const expected = db().orders.filter((o) => o.status === 'CLOSED_CONFIRMED').length
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(expected + 1)
    })
  })

  it('показывает разметку у выполненной заявки', async () => {
    const done = db().orders.find((o) => o.status === 'CLOSED_CONFIRMED')!

    renderWithProviders(<AppShell />, { route: `/orders?order=${done.id}` })

    const panel = await screen.findByRole('complementary', { name: 'Карточка заявки' })
    expect(await within(panel).findByText('Результат закрытия')).toBeInTheDocument()
    expect(within(panel).getByText('Факт подтверждён')).toBeInTheDocument()
  })
})

describe('полный цикл: подтверждение, работа, закрытие с разметкой', () => {
  it('проводит заявку от автосоздания до закрытия', async () => {
    const user = userEvent.setup()
    const order = db().orders.find((o) => o.status === 'AUTO_CREATED')!

    renderWithProviders(<AppShell />, { route: `/orders?order=${order.id}` })

    const panel = await screen.findByRole('complementary', { name: 'Карточка заявки' })
    expect(await within(panel).findByText('Создана автоматически')).toBeInTheDocument()

    /* 1. Диспетчер подтверждает автоматически созданную заявку. */
    await user.click(within(panel).getByRole('button', { name: 'Подтвердить заявку' }))
    await user.type(screen.getByLabelText(/Ответственный/), 'Иванов')
    await user.click(submitIn('Подтвердить заявку'))

    expect(await within(panel).findByText('Подтверждена')).toBeInTheDocument()

    /* 2. Бригада берёт заявку в работу. */
    await user.click(within(panel).getByRole('button', { name: 'Взять в работу' }))
    await user.type(screen.getByLabelText(/Бригада/), 'Бригада 7')
    await user.click(submitIn('Взять в работу'))

    expect(await within(panel).findByText('В работе')).toBeInTheDocument()

    /* 3. Закрытие — единственная форма со своим UI. */
    await user.click(within(panel).getByRole('button', { name: 'Закрыть заявку' }))

    // Без отметки «факт подтверждён» закрыть нельзя.
    await user.selectOptions(screen.getByLabelText(/Фактическая причина/), (
      screen.getAllByRole('option')[1] as HTMLOptionElement
    ).value)
    await user.type(screen.getByLabelText(/Что сделано/), 'дефект устранён на месте')
    await user.click(submitIn('Закрыть заявку'))
    expect(screen.getByText(/Отметьте, подтверждён ли факт/)).toBeInTheDocument()

    // С отметкой — закрывается.
    await user.click(screen.getByRole('radio', { name: /событие подтвердилось/i }))
    await user.click(submitIn('Закрыть заявку'))

    expect(await within(panel).findByText('Закрыта: факт подтверждён')).toBeInTheDocument()
    expect(await within(panel).findByText('Результат закрытия')).toBeInTheDocument()
    expect(within(panel).getByText('да')).toBeInTheDocument()
  })

  it('ведёт от прогноза к заявке по ссылке в карточке', async () => {
    const user = userEvent.setup()
    const withOrder = db().predictions.find((p) => p.orderId)!

    renderWithProviders(<AppShell />, { route: `/journal?prediction=${withOrder.id}` })

    const panel = await screen.findByRole('complementary', { name: 'Карточка прогноза' })
    await user.click(await within(panel).findByRole('link', { name: /Создана заявка/ }))

    await waitFor(() => expect(currentSearch()).toContain(`order=${withOrder.orderId}`))
  })
})
