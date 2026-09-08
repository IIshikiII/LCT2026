/**
 * Легенда карты. Уровни — из меты, поэтому новый уровень риска появляется в
 * легенде сам и с тем же цветом, что и точки.
 */
import type { AppMeta } from '@/shared/api/types'
import { levelColor, levelsBySeverity } from '@/shared/lib/risk'

export function MapLegend({ meta, total }: { meta: AppMeta; total: number }) {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 rounded border border-line bg-panel/95 px-2.5 py-2">
      <p className="mb-1.5 text-[11px] text-text-mute">Уровень риска · объектов: {total}</p>
      <ul className="flex flex-col gap-1">
        {levelsBySeverity(meta).map((level) => (
          <li key={level.code} className="flex items-center gap-2 text-[12px] text-text-dim">
            <span
              aria-hidden="true"
              className="size-2 rounded-full"
              style={{ background: levelColor(level.code, meta) }}
            />
            {level.label}
          </li>
        ))}
      </ul>
    </div>
  )
}
