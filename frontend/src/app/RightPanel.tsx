/**
 * Правая панель 420 px с карточкой прогноза или заявки.
 *
 * Не модальная: карта и таблица под ней остаются видны — диспетчер
 * сопоставляет карточку с положением объекта в списке или на карте (spec §4).
 *
 * Что показывать, определяется параметрами URL `?prediction=` и `?order=`,
 * поэтому ссылка с открытой карточкой восстанавливает то же состояние
 * (критерий приёмки spec §12).
 */
import { useEffect } from 'react'
import type { AppMeta } from '@/shared/api/types'
import { useSelected } from '@/shared/lib/urlState'
import { Button } from '@/shared/ui/Button'
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary'
import { Icon } from '@/shared/ui/Icon'
import { ErrorState } from '@/shared/ui/states'
import { OrderCard } from '@/card/OrderCard'
import { PredictionCard } from '@/card/PredictionCard'

export function RightPanel({ meta }: { meta: AppMeta }) {
  const [predictionId, selectPrediction] = useSelected('prediction')
  const [orderId, selectOrder] = useSelected('order')

  const open = Boolean(predictionId ?? orderId)

  const close = () => {
    if (predictionId) selectPrediction(undefined)
    if (orderId) selectOrder(undefined)
  }

  // Escape закрывает панель — обязательный минимум доступности.
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, predictionId, orderId])

  if (!open) return null

  return (
    <aside
      aria-label={predictionId ? 'Карточка прогноза' : 'Карточка заявки'}
      className="flex w-[420px] shrink-0 flex-col border-l border-line bg-panel"
    >
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
        <h2 className="text-[13px] font-medium text-text">
          {predictionId ? 'Прогноз' : 'Заявка'}
        </h2>
        <Button
          size="sm"
          kind="ghost"
          onClick={close}
          aria-label="Закрыть панель"
          title="Закрыть (Esc)"
          icon={<Icon name="close" size={14} />}
        />
      </div>

      <div className="min-h-0 flex-1">
        <ErrorBoundary
          key={predictionId ?? orderId}
          fallback={<ErrorState title="Карточку не удалось отобразить" />}
        >
          {predictionId ? (
            <PredictionCard id={predictionId} meta={meta} />
          ) : orderId ? (
            <OrderCard id={orderId} meta={meta} />
          ) : null}
        </ErrorBoundary>
      </div>
    </aside>
  )
}
