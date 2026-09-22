/**
 * Поведение стенд-ина бэкенда на уровне HTTP.
 *
 * Экранные тесты ходят через хуки и не видят разницы между «параметр учтён» и
 * «параметр молча выброшен». Здесь запрос идёт прямо в хендлер, поэтому
 * проверяется ровно то, что обязан повторить настоящий бэкенд
 * (backend/docs/04-api-required-by-frontend.md).
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { apiGet, apiPost, type QueryParams } from '@/shared/api/client'
import { endpoints } from '@/shared/api/endpoints'
import {
  FacilityCollectionSchema,
  LineCollectionSchema,
  WorkOrderSchema,
} from '@/shared/api/schemas'
import type { FacilityCollection, LineCollection, WorkOrder } from '@/shared/api/types'
import { db } from './db'
import { resetMockDb } from './handlers'

/** Запрос идёт через штатный клиент — тем же путём, что и в приложении. */
const facilities = (params?: QueryParams) =>
  apiGet<FacilityCollection>(endpoints.facilities(), FacilityCollectionSchema, { params })

beforeEach(() => {
  resetMockDb()
})

describe('GET /facilities', () => {
  it('отдаёт по одной точке на объект', async () => {
    const data = await facilities()
    const ids = data.features.map((f) => f.properties.facilityId)
    expect(ids.length).toBeGreaterThan(0)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('оставляет только объекты внутри bbox', async () => {
    const all = await facilities()
    const [lon, lat] = all.features[0]!.geometry.coordinates

    // Коробка вокруг одной точки: примерно 300 метров в каждую сторону.
    const half = 0.004
    const inside = await facilities({
      bbox: [lon - half, lat - half, lon + half, lat + half].join(','),
    })

    expect(inside.features.length).toBeGreaterThan(0)
    expect(inside.features.length).toBeLessThan(all.features.length)
    for (const feature of inside.features) {
      const [flon, flat] = feature.geometry.coordinates
      expect(Math.abs(flon - lon)).toBeLessThanOrEqual(half)
      expect(Math.abs(flat - lat)).toBeLessThanOrEqual(half)
    }
  })

  it('на пустой области отдаёт пустой список, а не всё подряд', async () => {
    const data = await facilities({ bbox: '0,0,1,1' })
    expect(data.features).toHaveLength(0)
  })

  it('не роняет выборку на битом bbox', async () => {
    const all = await facilities()
    const broken = await facilities({ bbox: 'что-то не то' })
    expect(broken.features).toHaveLength(all.features.length)
  })

  it('больше не возит трассы коллекторов вместе с точками', async () => {
    const data = (await facilities()) as FacilityCollection & { lines?: unknown }
    expect(data.lines).toBeUndefined()
  })
})

describe('GET /facilities/lines', () => {
  it('отдаёт трассы коллекторов отдельной ручкой', async () => {
    const data = await apiGet<LineCollection>(endpoints.facilityLines(), LineCollectionSchema)
    expect(data.type).toBe('FeatureCollection')
    expect(data.features.length).toBeGreaterThan(0)
    for (const feature of data.features) {
      expect(feature.geometry.type).toBe('LineString')
      expect(feature.properties.collector).toBeTruthy()
    }
  })
})

describe('POST /orders/{id}/actions/close', () => {
  const close = (id: string, body: Record<string, unknown>) =>
    apiPost<WorkOrder>(endpoints.orderAction(id, 'close'), WorkOrderSchema, body)

  it('засчитывает подтверждение только у настоящего boolean', async () => {
    const order = db().orders.find((o) => o.status === 'IN_PROGRESS')!

    const closed = await close(order.id, {
      actualCause: 'OTHER',
      // Строку шлёт только чужой клиент: форма закрытия отдаёт boolean.
      factConfirmed: 'true',
      comment: 'проверка типа',
    })

    // Строка не boolean значит факт не подтверждён, и статус это отражает.
    expect(closed.status).toBe('CLOSED_NOT_CONFIRMED')
    expect(closed.outcome?.factConfirmed).toBe(false)
  })

  it('засчитывает подтверждение при true', async () => {
    const order = db().orders.find((o) => o.status === 'IN_PROGRESS')!

    const closed = await close(order.id, {
      actualCause: 'OTHER',
      factConfirmed: true,
      comment: 'дефект устранён',
    })

    expect(closed.status).toBe('CLOSED_CONFIRMED')
    expect(closed.outcome?.factConfirmed).toBe(true)
  })
})
