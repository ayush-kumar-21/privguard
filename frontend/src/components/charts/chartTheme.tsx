import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'

/**
 * Shared chart chrome. Mark specs come from the dataviz method: 2px lines,
 * 8px markers, ~10% area washes, solid hairline grid one step off the surface,
 * and never a second y-axis.
 */
export const CHART = {
  series1: 'var(--series-1)',
  series2: 'var(--series-2)',
  grid: 'var(--grid)',
  axis: 'var(--axis)',
  danger: 'var(--danger)',
  dangerWeak: 'var(--danger-weak)',
  surface: 'var(--surface)',
  lineWidth: 2,
  areaOpacity: 0.1,
  tick: { fill: 'var(--text-faint)', fontSize: 11, fontFamily: 'var(--font-mono)' },
} as const

/** Tooltip shell, so every chart in the app speaks with the same voice. */
export function ChartTooltip({
  title,
  children,
}: {
  title: ReactNode
  children: ReactNode
}) {
  return (
    <div className="min-w-[9rem] rounded-sm border border-border bg-surface px-3 py-2 shadow-lg shadow-black/10 dark:shadow-black/40">
      <p className="num text-micro text-faint">{title}</p>
      <dl className="mt-1.5 grid gap-1">{children}</dl>
    </div>
  )
}

export function TooltipRow({
  swatch,
  label,
  value,
}: {
  swatch?: string
  label: string
  value: ReactNode
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="flex items-center gap-1.5 text-micro text-muted">
        {swatch ? (
          <span
            aria-hidden
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: swatch }}
          />
        ) : null}
        {label}
      </dt>
      <dd className="num text-micro text-text">{value}</dd>
    </div>
  )
}

/**
 * Legend for two or more series. Identity is the swatch beside the text, never
 * the text colour itself.
 */
export function ChartLegend({
  items,
  className,
}: {
  items: { color: string; label: string }[]
  className?: string
}) {
  return (
    <ul className={cn('flex flex-wrap items-center gap-x-5 gap-y-1.5', className)}>
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2 text-micro text-muted">
          <span
            aria-hidden
            className="h-0.5 w-4 shrink-0 rounded-full"
            style={{ background: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  )
}
