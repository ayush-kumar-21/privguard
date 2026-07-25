import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

const badge = cva(
  // nowrap: chips live in table cells that already scroll horizontally, so a
  // two-line "rejected: budget" would just make every row taller.
  'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-micro font-medium leading-5',
  {
    variants: {
      tone: {
        neutral: 'border-border bg-surface-2 text-muted',
        accent: 'border-transparent bg-accent-weak text-accent',
        ok: 'border-transparent bg-ok-weak text-ok',
        warn: 'border-transparent bg-warn-weak text-warn',
        danger: 'border-transparent bg-danger-weak text-danger',
        outline: 'border-border-strong bg-transparent text-muted',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badge> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone }), className)} {...props} />
}

/** A 6px status dot that inherits the badge tone colour. */
export function Dot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('h-1.5 w-1.5 shrink-0 rounded-full bg-current', className)}
    />
  )
}

export { badge as badgeVariants }
