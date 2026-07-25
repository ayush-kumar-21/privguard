import { cn } from '../../lib/cn'
import { budgetBand, rho as fmtRho } from '../../lib/format'
import { useCountUp } from '../../lib/useCountUp'

const BAR: Record<string, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
}

const TEXT: Record<string, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
}

export function bandTextClass(remaining: number, total: number) {
  return TEXT[budgetBand(remaining, total)]
}

/**
 * Thin remaining-budget bar. The colour band is the whole point: an analyst
 * should read "how much privacy budget is left" without reading a number.
 */
export function RhoMeter({
  remaining,
  total,
  className,
  height = 'h-1.5',
  animate = true,
}: {
  remaining: number
  total: number
  className?: string
  height?: string
  animate?: boolean
}) {
  const band = budgetBand(remaining, total)
  const targetPct = total > 0 ? Math.max(0, Math.min(1, remaining / total)) * 100 : 0
  const animated = useCountUp(targetPct, animate ? 520 : 0)

  return (
    <div
      className={cn('w-full overflow-hidden rounded-full bg-surface-2', height, className)}
      role="meter"
      aria-valuenow={Number(remaining.toFixed(3))}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-label={`Privacy budget remaining: rho ${fmtRho(remaining)} of ${fmtRho(total)}`}
    >
      <div
        className={cn('h-full rounded-full transition-colors duration-300', BAR[band])}
        style={{ width: `${animated}%` }}
      />
    </div>
  )
}

/** Compact topbar variant: label, bar and figures on one 220px line. */
export function BudgetMiniMeter({
  remaining,
  total,
  className,
}: {
  remaining: number
  total: number
  className?: string
}) {
  const band = budgetBand(remaining, total)
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <span className="label-micro hidden sm:inline">budget</span>
      <div className="flex items-baseline gap-1">
        <span className={cn('num text-small font-medium', TEXT[band])}>
          {fmtRho(remaining, 2)}
        </span>
        <span className="num text-micro text-faint">/ {fmtRho(total, 2)}</span>
      </div>
      <RhoMeter remaining={remaining} total={total} className="w-16 sm:w-24" height="h-1" />
    </div>
  )
}
