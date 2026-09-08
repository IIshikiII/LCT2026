/**
 * Маршруты и состав рельса.
 *
 * Периметр — ровно четыре экрана (spec §1). Выключенный флагом экран пропадает
 * и из роутера, и из рельса, поэтому список пунктов и список маршрутов строятся
 * из одного источника.
 *
 * Карта загружается лениво: MapLibre — самая тяжёлая зависимость проекта, и
 * держать её в первом чанке ради экрана, который открывают не всегда, незачём.
 * Побочная польза — тестам, не касающимся карты, не нужен WebGL.
 */
import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import type { AppMeta } from '@/shared/api/types'
import { features, type FeatureKey } from '@/shared/config/features'
import type { IconName } from '@/shared/ui/Icon'
import { Spinner } from '@/shared/ui/states'
import { DashboardScreen } from '@/screens/dashboard/DashboardScreen'
import { JournalScreen } from '@/screens/journal/JournalScreen'
import { OrdersScreen } from '@/screens/orders/OrdersScreen'

const MapScreen = lazy(() =>
  import('@/screens/map/MapScreen').then((module) => ({ default: module.MapScreen })),
)

export interface NavItem {
  key: FeatureKey
  path: string
  label: string
  icon: IconName
}

export const NAV_ITEMS: NavItem[] = [
  { key: 'dashboard', path: '/', label: 'Дашборд рисков', icon: 'dashboard' },
  { key: 'map', path: '/map', label: 'Карта объектов', icon: 'map' },
  { key: 'journal', path: '/journal', label: 'Журнал прогнозов', icon: 'journal' },
  { key: 'orders', path: '/orders', label: 'Заявки', icon: 'orders' },
]

export function enabledNavItems(): NavItem[] {
  return NAV_ITEMS.filter((item) => features[item.key])
}

function Loading() {
  return (
    <div className="flex h-full items-center justify-center">
      <Spinner />
    </div>
  )
}

export function AppRoutes({ meta }: { meta: AppMeta }) {
  const fallbackPath = enabledNavItems()[0]?.path ?? '/'

  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        {features.dashboard ? <Route path="/" element={<DashboardScreen meta={meta} />} /> : null}
        {features.map ? <Route path="/map" element={<MapScreen meta={meta} />} /> : null}
        {features.journal ? <Route path="/journal" element={<JournalScreen meta={meta} />} /> : null}
        {features.orders ? <Route path="/orders" element={<OrdersScreen meta={meta} />} /> : null}
        <Route path="*" element={<Navigate to={fallbackPath} replace />} />
      </Routes>
    </Suspense>
  )
}
