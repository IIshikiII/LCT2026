/**
 * Общая рамка блока карточки: заголовок и тело.
 *
 * Вынесена отдельно, чтобы новый тип блока не переизобретал отступы и подпись —
 * добавление блока должно оставаться правкой на один файл.
 */
import type { ReactNode } from 'react'

export function BlockFrame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-line px-3 py-3 last:border-b-0">
      <h3 className="mb-2 text-[12px] font-medium text-text-mute">{title}</h3>
      {children}
    </section>
  )
}

/** Сообщение вместо блока, который не удалось нарисовать. */
export function BlockError({ title }: { title: string }) {
  return (
    <BlockFrame title={title}>
      <p className="text-[13px] text-text-mute">
        Блок не удалось отобразить. Остальная карточка работает — подробности в консоли.
      </p>
    </BlockFrame>
  )
}
