/**
 * Number and date formatters. Everything these return is rendered inside a
 * `.num` element so figures stay monospaced and tabular across rows.
 */

/** Privacy budget in zCDP rho: always 3 decimals so columns line up. */
export function rho(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toFixed(digits)
}

/** Equivalent epsilon, which is coarser than rho in practice. */
export function eps(value: number | null | undefined, digits = 3): string {
  return rho(value, digits)
}

/** Compact integers with thin grouping: 12,480. */
export function int(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return Math.round(value).toLocaleString('en-US')
}

/** Noisy aggregate answers: keep 2 decimals unless the value is integral. */
export function metric(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return '--'
  if (typeof value === 'string') return value
  if (Number.isNaN(value)) return '--'
  return Number.isInteger(value)
    ? value.toLocaleString('en-US')
    : value.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
}

export function currency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return Number(value).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  })
}

export function pct(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return '--'
  return `${(fraction * 100).toFixed(digits)}%`
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  if (value < 1000) return `${value.toFixed(0)}ms`
  return `${(value / 1000).toFixed(2)}s`
}

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const DATE_ONLY = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '--' : DATE_TIME.format(d)
}

export function date(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '--' : DATE_ONLY.format(d)
}

/** "4m ago" for the audit feed, falling back to an absolute stamp past a day. */
export function relative(iso: string | null | undefined): string {
  if (!iso) return '--'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '--'
  const secs = Math.round((Date.now() - then) / 1000)
  if (secs < 45) return 'just now'
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`
  return dateTime(iso)
}

/** Budget health band, used for both meter colour and tile colour. */
export type BudgetBand = 'ok' | 'warn' | 'danger'

export function budgetBand(remaining: number, total: number): BudgetBand {
  if (total <= 0) return 'danger'
  const fraction = remaining / total
  if (fraction <= 0.1) return 'danger'
  if (fraction <= 0.35) return 'warn'
  return 'ok'
}

/** Collapse whitespace so a multi-line SQL string fits one table cell. */
export function oneLine(sql: string, max = 92): string {
  const flat = sql.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}...` : flat
}
