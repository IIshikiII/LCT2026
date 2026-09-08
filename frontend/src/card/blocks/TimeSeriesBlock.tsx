/**
 * График показаний с вертикальной отметкой момента формирования прогноза.
 *
 * Каждый ряд рисуется отдельным маленьким графиком: единицы измерения у рядов
 * разные (°C и % затемнения не кладутся на одну ось), а общий график с двумя
 * осями читается хуже, чем два простых.
 */
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { z } from 'zod'
import type { CardBlock } from '@/shared/api/types'
import { fmtDateTime, fmtTime } from '@/shared/lib/format'
import { BlockFrame } from './BlockFrame'
import { GenericBlock } from './GenericBlock'

const Schema = z.looseObject({
  series: z.array(
    z.looseObject({
      name: z.string(),
      unit: z.string().optional(),
      points: z.array(z.looseObject({ t: z.string(), v: z.number() })),
    }),
  ),
  markerAt: z.string().optional(),
})

const axisStyle = { fill: 'var(--text-mute)', fontSize: 11 }

export function TimeSeriesBlock({ block }: { block: CardBlock }) {
  const parsed = Schema.safeParse(block.data)
  if (!parsed.success) return <GenericBlock block={block} />

  const { series, markerAt } = parsed.data

  return (
    <BlockFrame title={block.title}>
      <div className="flex flex-col gap-3">
        {series.map((row) => {
          const data = row.points.map((p) => ({ t: new Date(p.t).getTime(), v: p.v }))
          return (
            <figure key={row.name} className="m-0">
              <figcaption className="mb-1 text-[12px] text-text-dim">
                {row.name}
                {row.unit ? <span className="text-text-mute">, {row.unit}</span> : null}
              </figcaption>
              <div className="h-28 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: -18 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="2 3" vertical={false} />
                    <XAxis
                      dataKey="t"
                      type="number"
                      domain={['dataMin', 'dataMax']}
                      tickFormatter={(value: number) => fmtTime(new Date(value))}
                      tick={axisStyle}
                      stroke="var(--line)"
                      minTickGap={40}
                    />
                    <YAxis tick={axisStyle} stroke="var(--line)" width={44} />
                    <Tooltip
                      contentStyle={{
                        background: 'var(--panel)',
                        border: '1px solid var(--line-strong)',
                        borderRadius: 4,
                        fontSize: 12,
                      }}
                      labelFormatter={(value) => fmtDateTime(new Date(Number(value)))}
                    />
                    {markerAt ? (
                      <ReferenceLine
                        x={new Date(markerAt).getTime()}
                        stroke="var(--risk-high)"
                        strokeDasharray="3 3"
                        label={{ value: 'прогноз', fill: 'var(--text-mute)', fontSize: 10 }}
                      />
                    ) : null}
                    <Line
                      type="monotone"
                      dataKey="v"
                      name={row.name}
                      stroke="var(--text-dim)"
                      strokeWidth={1.5}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </figure>
          )
        })}
      </div>
    </BlockFrame>
  )
}
