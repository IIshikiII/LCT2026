/**
 * Уведомления о тревоге (ТЗ §10). Тревогу решает сервер, интерфейс только
 * показывает её и напоминает с шагом, который пришёл в ответе.
 */
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { db } from '@/mocks/db'
import { hide, journalLink, remember } from '@/shared/lib/alertSchedule'
import { server } from '@/test/msw'
import { renderWithProviders } from '@/test/renderWithProviders'
import { AlertBadge, AlertToasts } from './Alerts'

const ALERT = {
  code: 'critical_untaken',
  level: 'CRITICAL',
  count: 3,
  title: '3 критических инцидента не взяты в работу',
  hint: 'Возьмите инциденты в работу в журнале прогнозов',
  filter: { level: ['CRITICAL'], status: ['NEW'] },
  repeatMinutes: 5,
}
const MIN = 60_000

describe('расписание напоминаний', () => {
  it('новая тревога показывается сразу', () => {
    expect(remember(undefined, 3, 5, 0)).toMatchObject({ hidden: false, count: 3 })
  })

  it('скрытая тревога возвращается через шаг повтора после скрытия', () => {
    const hidden = hide(remember(undefined, 3, 5, 0), 1 * MIN)
    expect(remember(hidden, 3, 5, 4 * MIN).hidden).toBe(true)
    expect(remember(hidden, 3, 5, 6 * MIN).hidden).toBe(false)
  })

  it('рост числа инцидентов показывает тревогу сразу', () => {
    const hidden = hide(remember(undefined, 3, 5, 0), 0)
    const next = remember(hidden, 4, 5, MIN)
    expect(next.hidden).toBe(false)
    expect(next.pulse).toBe(1)
  })

  it('видимая тревога напоминает о себе каждые пять минут', () => {
    const first = remember(undefined, 3, 5, 0)
    expect(remember(first, 3, 5, 5 * MIN).pulse).toBe(1)
  })

  it('ссылка ведёт в журнал с фильтром тревоги', () => {
    expect(journalLink(ALERT.filter)).toBe('/journal?level=CRITICAL&status=NEW')
  })
})

describe('уведомление в интерфейсе', () => {
  it('показывает тревогу из ответа сервера и её число в шапке', async () => {
    server.use(http.get('*/api/v1/alerts', () => HttpResponse.json([ALERT])))
    renderWithProviders(
      <>
        <AlertBadge meta={db().meta} />
        <AlertToasts meta={db().meta} />
      </>,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(ALERT.title)
    expect(screen.getByRole('link', { name: /не в работе/ })).toHaveAttribute(
      'href',
      '/journal?level=CRITICAL&status=NEW',
    )
    await waitFor(() => expect(document.title).toMatch(/^\(3\)/))
  })

  it('«Напомнить позже» убирает окно, плашка в шапке остаётся', async () => {
    server.use(http.get('*/api/v1/alerts', () => HttpResponse.json([ALERT])))
    renderWithProviders(
      <>
        <AlertBadge meta={db().meta} />
        <AlertToasts meta={db().meta} />
      </>,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Напомнить через 5 мин' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /не в работе/ })).toBeInTheDocument()
  })

  it('без тревоги ничего не показывает', async () => {
    server.use(http.get('*/api/v1/alerts', () => HttpResponse.json([])))
    renderWithProviders(
      <>
        <AlertBadge meta={db().meta} />
        <AlertToasts meta={db().meta} />
      </>,
    )
    await waitFor(() => expect(document.title).not.toMatch(/^\(\d+\)/))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /не в работе/ })).not.toBeInTheDocument()
  })
})
