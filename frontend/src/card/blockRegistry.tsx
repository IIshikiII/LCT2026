/**
 * Реестр блоков карточки — точка расширения №1 (docs/03-extension-points.md).
 *
 * Тело карточки приходит из API массивом `{ type, title, data }`. Реестр
 * сопоставляет `type` компоненту; для неизвестного типа работает GenericBlock.
 * Добавить тип блока = один файл плюс одна строка здесь (ADR 0003).
 *
 * ErrorBoundary вокруг каждого блока обязателен: данные приходят с бэкенда, их
 * форму фронт не контролирует, и сломанный блок не должен уносить карточку.
 */
import type { FC } from 'react'
import type { CardBlock } from '@/shared/api/types'
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary'
import { BlockError } from './blocks/BlockFrame'
import { FactorsBlock } from './blocks/FactorsBlock'
import { GenericBlock } from './blocks/GenericBlock'
import { KeyValueBlock } from './blocks/KeyValueBlock'
import { TableBlock } from './blocks/TableBlock'
import { TimeSeriesBlock } from './blocks/TimeSeriesBlock'
import { TimelineBlock } from './blocks/TimelineBlock'

export type BlockView = FC<{ block: CardBlock }>

export const blockRegistry: Record<string, BlockView> = {
  factors: FactorsBlock,
  timeseries: TimeSeriesBlock,
  timeline: TimelineBlock,
  table: TableBlock,
  keyvalue: KeyValueBlock,
}

export function BlockRenderer({ block }: { block: CardBlock }) {
  const View = blockRegistry[block.type] ?? GenericBlock
  return (
    <ErrorBoundary key={block.type} fallback={<BlockError title={block.title} />}>
      <View block={block} />
    </ErrorBoundary>
  )
}

/** Тело карточки целиком. */
export function BlockList({ blocks }: { blocks: CardBlock[] }) {
  if (blocks.length === 0) {
    return <p className="px-3 py-4 text-[13px] text-text-mute">Объяснение прогноза не пришло.</p>
  }
  return (
    <>
      {blocks.map((block, index) => (
        <BlockRenderer key={`${block.type}-${index}`} block={block} />
      ))}
    </>
  )
}
