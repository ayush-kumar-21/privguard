import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

/** Wrapper that keeps wide tables scrolling inside themselves, never the page. */
export function TableScroll({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('scroll-x', className)} {...props} />
}

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <table
      className={cn('w-full border-collapse text-small', className)}
      {...props}
    />
  )
}

export function THead({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn('border-b border-border', className)} {...props} />
}

export function TBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn('divide-y divide-border', className)} {...props} />
}

export function TR({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn('transition-colors duration-100 hover:bg-surface-2/60', className)}
      {...props}
    />
  )
}

export function TH({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        'whitespace-nowrap px-4 py-2.5 text-left align-middle',
        'text-micro font-medium uppercase tracking-[0.06em] text-faint',
        className,
      )}
      {...props}
    />
  )
}

export function TD({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn(
        // nowrap keeps every row the same height. Wide cells either truncate
        // or ride the wrapper's horizontal scroll.
        'whitespace-nowrap px-4 py-2.5 align-middle text-small text-text',
        className,
      )}
      {...props}
    />
  )
}
