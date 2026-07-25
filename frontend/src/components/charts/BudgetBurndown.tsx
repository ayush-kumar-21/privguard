import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { epsFromRho } from '../../lib/accounting'
import { dateTime, eps as fmtEps, rho as fmtRho } from '../../lib/format'
import type { QueryAudit } from '../../lib/types'
import { CHART, ChartTooltip, TooltipRow } from './chartTheme'

interface Point {
  t: number
  cumulative: number
  charged: number
  label: string
}

/** Round a raw step up to 1, 2, 2.5 or 5 times a power of ten. */
function niceStep(raw: number): number {
  if (raw <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const scaled = raw / magnitude
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 2.5 ? 2.5 : scaled <= 5 ? 5 : 10
  return step * magnitude
}

/** Axis ticks on round numbers, always including the exhaustion ceiling. */
function axisTicks(max: number, ceiling: number): number[] {
  const step = niceStep(max / 4)
  const ticks: number[] = []
  for (let value = 0; value <= max + 1e-9; value += step) {
    ticks.push(Number(value.toFixed(6)))
  }
  if (!ticks.some((tick) => Math.abs(tick - ceiling) < step / 8)) ticks.push(ceiling)
  return ticks.sort((a, b) => a - b)
}

/**
 * Cumulative zCDP spend against the account ceiling.
 *
 * One y-axis, in rho. The UI plan sketched an epsilon line on a second axis,
 * but epsilon is a deterministic function of rho, so a second scale would
 * invent a relationship the data does not have (dataviz: no dual axis). The
 * epsilon equivalent rides the tooltip and the panel header instead, where it
 * is exact rather than eyeballed off an arbitrary scale.
 */
export function BudgetBurndown({
  queries,
  rhoTotal,
  delta,
  height = 260,
}: {
  queries: QueryAudit[]
  rhoTotal: number
  delta: number | null
  height?: number
}) {
  const data = useMemo<Point[]>(() => {
    const charged = [...queries]
      .filter((query) => query.rho_charged > 0)
      .sort(
        (a, b) =>
          new Date(a.submitted_at).getTime() - new Date(b.submitted_at).getTime(),
      )

    let running = 0
    const points: Point[] = charged.map((query) => {
      running += query.rho_charged
      return {
        t: new Date(query.submitted_at).getTime(),
        cumulative: Number(running.toFixed(6)),
        charged: query.rho_charged,
        label: dateTime(query.submitted_at),
      }
    })

    // Anchor the series at zero so the first spend reads as a step, not a jump.
    if (points.length > 0) {
      const first = points[0]
      points.unshift({
        t: first.t - 1000,
        cumulative: 0,
        charged: 0,
        label: 'start',
      })
    }
    return points
  }, [queries])

  // Headroom above the ceiling so the exhaustion band is always visible.
  const yMax = Math.max(rhoTotal * 1.12, (data.at(-1)?.cumulative ?? 0) * 1.12, 0.1)

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
          <defs>
            <linearGradient id="rho-wash" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CHART.series1} stopOpacity={0.16} />
              <stop offset="100%" stopColor={CHART.series1} stopOpacity={0.02} />
            </linearGradient>
          </defs>

          <CartesianGrid stroke={CHART.grid} strokeDasharray="0" vertical={false} />

          {/* Everything above rho_total is budget the account does not have. */}
          <ReferenceArea
            y1={rhoTotal}
            y2={yMax}
            fill={CHART.danger}
            fillOpacity={0.07}
            stroke="none"
          />
          <ReferenceLine
            y={rhoTotal}
            stroke={CHART.danger}
            strokeWidth={1}
            label={{
              value: `exhausted at rho ${fmtRho(rhoTotal, 2)}`,
              position: 'insideTopRight',
              fill: 'var(--text-faint)',
              fontSize: 11,
              fontFamily: 'var(--font-mono)',
              dy: -4,
            }}
          />

          <XAxis
            dataKey="t"
            type="number"
            domain={['dataMin', 'dataMax']}
            scale="time"
            tickFormatter={(value: number) => dateTime(new Date(value).toISOString())}
            tick={CHART.tick}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            minTickGap={44}
          />
          <YAxis
            domain={[0, yMax]}
            ticks={axisTicks(yMax, rhoTotal)}
            tick={CHART.tick}
            tickLine={false}
            axisLine={false}
            width={48}
            tickFormatter={(value: number) => fmtRho(value, 2)}
          />

          <Tooltip
            cursor={{ stroke: CHART.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const point = payload[0].payload as Point
              return (
                <ChartTooltip title={point.label}>
                  <TooltipRow
                    swatch={CHART.series1}
                    label="cumulative rho"
                    value={fmtRho(point.cumulative)}
                  />
                  <TooltipRow label="this query" value={fmtRho(point.charged)} />
                  <TooltipRow
                    label="equivalent eps"
                    value={fmtEps(epsFromRho(point.cumulative, delta ?? 1e-6), 2)}
                  />
                  <TooltipRow
                    label="remaining"
                    value={fmtRho(Math.max(0, rhoTotal - point.cumulative))}
                  />
                </ChartTooltip>
              )
            }}
          />

          <Area
            type="stepAfter"
            dataKey="cumulative"
            stroke={CHART.series1}
            strokeWidth={CHART.lineWidth}
            strokeLinejoin="round"
            strokeLinecap="round"
            fill="url(#rho-wash)"
            activeDot={{
              r: 4,
              fill: CHART.series1,
              stroke: CHART.surface,
              strokeWidth: 2,
            }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
