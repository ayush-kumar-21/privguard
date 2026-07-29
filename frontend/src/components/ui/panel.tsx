import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'

/**
 * The single container in the system. Sections are grouped with borders and
 * negative space, never with a card inside another card.
 */
export function Panel({
  children,
  className,
  inset = false,
}: {
  children: ReactNode
  className?: string
  /** Inset panels drop their own padding so a table can span edge to edge. */
  inset?: boolean
}) {
  return (
    <section
      className={cn(
        // One step of depth, no more. Each theme weights the two halves of the
        // token differently: on light the drop shadow does the work, on dark
        // (where a shadow is invisible) it is the lit top edge.
        'rounded-lg border border-border bg-surface shadow-panel-edge',
        inset ? '' : 'p-5',
        className,
      )}
    >
      {children}
    </section>
  )
}

export function PanelHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header
      className={cn(
        'flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-3.5',
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="text-h3 text-text">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-micro text-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  )
}

/** Page-level heading used at the top of every route. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-display text-text">{title}</h1>
        {description ? (
          <p className="mt-1.5 max-w-[62ch] text-body leading-relaxed text-muted">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  )
}
