import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { metric } from '../../lib/format'
import { AxisTick, CHART, ChartLegend, ChartTooltip, TooltipRow } from './chartTheme'

export interface AveragingPoint {
  k: number
  memoised: number
  independent: number
}

/**
 * What repetition buys an attacker.
 *
 * Independent draws: the standard error of the running mean falls as 1/sqrt(k),
 * so enough repeats recover the true answer. Memoised: the fingerprint replays
 * one answer forever, so k never rises above 1 and the error never moves. Two
 * series, one axis, legend plus an end label on each line.
 */
export function AveragingChart({
  data,
  height = 260,
}: {
  data: AveragingPoint[]
  height?: number
}) {
  const last = data.at(-1)

  return (
    <div className="grid gap-3">
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          {/* The end labels anchor inward, so the plot keeps the width that
              used to be reserved as empty right margin. */}
          <LineChart data={data} margin={{ top: 10, right: 12, bottom: 4, left: -6 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="0" vertical={false} />

            <XAxis
              dataKey="k"
              type="number"
              domain={[1, 'dataMax']}
              tick={<AxisTick />}
              tickLine={false}
              axisLine={{ stroke: CHART.axis }}
              label={{
                value: 'queries issued',
                position: 'insideBottom',
                offset: -2,
                fill: 'var(--text-faint)',
                fontSize: 11,
              }}
            />
            <YAxis
              tick={CHART.tick}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(value: number) => value.toFixed(1)}
            />

            <Tooltip
              cursor={{ stroke: CHART.axis, strokeWidth: 1 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const point = payload[0].payload as AveragingPoint
                return (
                  <ChartTooltip title={`after ${point.k} queries`}>
                    <TooltipRow
                      swatch={CHART.series2}
                      label="independent noise"
                      value={metric(point.independent)}
                    />
                    <TooltipRow
                      swatch={CHART.series1}
                      label="memoised (PrivGuard)"
                      value={metric(point.memoised)}
                    />
                  </ChartTooltip>
                )
              }}
            />

            <Line
              dataKey="independent"
              stroke={CHART.series2}
              strokeWidth={CHART.lineWidth}
              strokeLinecap="round"
              dot={false}
              activeDot={{
                r: 4,
                fill: CHART.series2,
                stroke: CHART.surface,
                strokeWidth: 2,
              }}
              isAnimationActive={false}
            />
            <Line
              dataKey="memoised"
              stroke={CHART.series1}
              strokeWidth={CHART.lineWidth}
              strokeLinecap="round"
              dot={false}
              activeDot={{
                r: 4,
                fill: CHART.series1,
                stroke: CHART.surface,
                strokeWidth: 2,
              }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <ChartLegend
          items={[
            { color: CHART.series2, label: 'independent noise per query' },
            { color: CHART.series1, label: 'memoised answer (PrivGuard)' },
          ]}
        />
        {last ? (
          <p className="num text-micro text-faint">
            at k={last.k}: {metric(last.independent)} vs {metric(last.memoised)}
          </p>
        ) : null}
      </div>
    </div>
  )
}
