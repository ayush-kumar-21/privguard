import { ArrowClockwise, WarningOctagon, type Icon } from '@phosphor-icons/react'
import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'
import { Button } from '../ui/button'
import { TD, TR } from '../ui/table'

/** Neutral shimmer block. Never a spinner where a shape is known in advance. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-sm bg-surface-2',
        'after:absolute after:inset-0 after:-translate-x-full after:animate-shimmer',
        'after:bg-gradient-to-r after:from-transparent after:via-black/[0.045] after:to-transparent',
        'dark:after:via-white/[0.05]',
        className,
      )}
    />
  )
}

/** Skeleton rows sized to the real column widths so nothing jumps on load. */
export function SkeletonRows({
  rows = 8,
  widths,
}: {
  rows?: number
  widths: string[]
}) {
  return (
    <>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <TR key={rowIndex} className="hover:bg-transparent">
          {widths.map((width, cellIndex) => (
            <TD key={cellIndex}>
              <Skeleton className={cn('h-3.5', width)} />
            </TD>
          ))}
        </TR>
      ))}
    </>
  )
}

export function EmptyState({
  icon: IconComponent,
  title,
  description,
  action,
  className,
}: {
  icon?: Icon
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 px-6 py-14 text-center',
        className,
      )}
    >
      {IconComponent ? (
        <IconComponent size={22} weight="regular" className="text-faint" aria-hidden />
      ) : null}
      <p className="font-serif text-h3 text-text">{title}</p>
      {description ? (
        <p className="max-w-[46ch] text-small text-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}

export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message: string
  onRetry?: () => void
  className?: string
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-2.5 px-6 py-12 text-center',
        className,
      )}
    >
      <WarningOctagon size={22} weight="regular" className="text-danger" aria-hidden />
      <p className="font-serif text-h3 text-text">Could not load this view</p>
      <p className="max-w-[52ch] text-small text-muted">{message}</p>
      {onRetry ? (
        <Button size="sm" variant="secondary" onClick={onRetry} className="mt-1">
          <ArrowClockwise size={13} weight="bold" aria-hidden />
          Retry
        </Button>
      ) : null}
    </div>
  )
}

/** Inline error banner for forms and query results, kept close to the cause. */
export function InlineError({
  message,
  className,
}: {
  message: ReactNode
  className?: string
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-2 rounded-sm border border-danger/35 bg-danger-weak px-3 py-2',
        'text-small text-danger',
        className,
      )}
    >
      <WarningOctagon size={15} weight="fill" className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0 break-words">{message}</span>
    </div>
  )
}
