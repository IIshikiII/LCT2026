/**
 * Список объектов группы на карте.
 *
 * Сама карта тестами не покрыта: MapLibre требует WebGL, которого в jsdom нет
 * (ADR 0008). Панель от карты не зависит — ей отдают готовый список объектов,
 * — поэтому проверяется именно она: что показывает, что отдаёт по нажатию и
 * как ведёт себя на неполных данных.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FacilityFeature } from '@/shared/api/types'
import { FALLBACK_META } from '@/shared/config/fallbacks'
import { ClusterPanel } from './ClusterPanel'

function feature(over: Partial<FacilityFeature['properties']> = {}): FacilityFeature {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [37.61, 55.75] },
    properties: {
      facilityId: 'F-0001',
      predictionId: 'P-1',
      direction: 'UNAUTHORIZED_ACCESS',
      level: 'HIGH',
      probability: 0.63,
      address: 'Коллектор северо-восточный, пикет 148',
      collector: 'K-SVAO-1',
      ...over,
    },
  }
}

describe('список объектов группы', () => {
  it('называет число объектов и показывает их адреса', () => {
    render(
      <ClusterPanel
        items={[feature(), feature({ facilityId: 'F-0002', address: 'Пикет 149' })]}
        meta={FALLBACK_META}
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading')).toHaveTextContent('Объектов в группе: 2')
    expect(screen.getByText('Коллектор северо-восточный, пикет 148')).toBeInTheDocument()
    expect(screen.getByText('Пикет 149')).toBeInTheDocument()
  })

  it('отдаёт нажатый объект целиком — вызвавшему нужны и прогноз, и координаты', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const item = feature()
    render(
      <ClusterPanel items={[item]} meta={FALLBACK_META} onPick={onPick} onClose={vi.fn()} />,
    )

    await user.click(screen.getByRole('button', { name: /пикет 148/i }))

    expect(onPick).toHaveBeenCalledWith(item)
  })

  it('помечает строку открытого прогноза', () => {
    render(
      <ClusterPanel
        items={[feature(), feature({ facilityId: 'F-0002', predictionId: 'P-2' })]}
        meta={FALLBACK_META}
        selectedId="P-2"
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const marked = screen.getAllByRole('button').filter((node) => node.ariaCurrent === 'true')
    expect(marked).toHaveLength(1)
  })

  it('переживает объект без прогноза: адрес есть, остального может не быть', () => {
    // Точка приходит из карты, а не из журнала: у объекта может не оказаться
    // ни прогноза, ни уровня. Падать на этом панель не имеет права.
    render(
      <ClusterPanel
        items={[
          {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [37.61, 55.75] },
            properties: { facilityId: 'F-0009' },
          },
        ]}
        meta={FALLBACK_META}
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /F-0009/ })).toBeInTheDocument()
  })

  it('закрывается своей кнопкой', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <ClusterPanel items={[feature()]} meta={FALLBACK_META} onPick={vi.fn()} onClose={onClose} />,
    )

    await user.click(screen.getByRole('button', { name: 'Закрыть список' }))

    expect(onClose).toHaveBeenCalledOnce()
  })
})
