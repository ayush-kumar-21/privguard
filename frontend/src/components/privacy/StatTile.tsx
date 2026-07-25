import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'
import { Skeleton } from '../states'

/**
 * Plain stat, not a card. Tiles are separated by rules and space so four of
 * them read as one instrument panel instead of four boxes.
 */
export function StatTile({
  label,
  value,
  unit,
  detail,
  tone = 'default',
  loading = false,
  className,
}: {
  label: string
  value: ReactNode
  unit?: ReactNode
  detail?: ReactNode
  tone?: 'default' | 'ok' | 'warn' | 'danger' | 'accent'
  loading?: boolean
  className?: string
}) {
  const toneClass = {
    default: 'text-text',
    ok: 'text-ok',
    warn: 'text-warn',
    danger: 'text-danger',
    accent: 'text-accent',
  }[tone]

  return (
    <div className={cn('flex min-w-0 flex-col gap-2 bg-surface px-5 py-4', className)}>
      <span className="label-micro">{label}</span>
      {loading ? (
        <Skeleton className="h-7 w-24" />
      ) : (
        <div className="flex items-baseline gap-1.5">
          <span className={cn('num text-[26px] font-medium leading-none', toneClass)}>
            {value}
          </span>
          {unit ? <span className="num text-small text-faint">{unit}</span> : null}
        </div>
      )}
      {detail ? (
        <div className="min-h-[18px] text-micro text-muted">{loading ? null : detail}</div>
      ) : null}
    </div>
  )
}

/**
 * Row container. The 1px gap over a border-coloured backdrop draws the
 * dividers, which stays correct at every breakpoint without nth-child rules.
 */
export function StatRow({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border',
        'sm:grid-cols-2 lg:grid-cols-4',
        className,
      )}
    >
      {children}
    </div>
  )
}
