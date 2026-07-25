import { cn } from '../../lib/cn'
import { useCountUp } from '../../lib/useCountUp'
import { Tooltip } from '../ui/misc'

const MECHANISM_LABEL: Record<string, string> = {
  gaussian: 'Gaussian',
  laplace: 'Laplace',
  discrete_laplace: 'Discrete Laplace',
  cache: 'Memoised',
  none: 'No noise',
}

/**
 * The price tag on a query: rho cost plus the mechanism that will produce the
 * noise. It counts up when the estimate changes so the cost of an edit to the
 * SQL is visible before the query runs.
 */
export function RhoBadge({
  rho,
  mechanism,
  label = 'rho cost',
  free = false,
  className,
}: {
  rho: number
  mechanism?: string
  label?: string
  /** Cache hits cost nothing; render that as an explicit state, not a zero. */
  free?: boolean
  className?: string
}) {
  const animated = useCountUp(rho)
  const mechanismLabel = mechanism ? MECHANISM_LABEL[mechanism] ?? mechanism : null

  return (
    <Tooltip
      content={
        free
          ? 'Memoised answer. Replaying a fingerprint costs no budget, so repeating a query cannot average the noise away.'
          : 'zCDP budget this query reserves. Charged only if it executes.'
      }
    >
      <div
        className={cn(
          'inline-flex flex-col gap-0.5 rounded-sm border border-border bg-surface-2 px-3 py-2',
          className,
        )}
      >
        <span className="label-micro">{label}</span>
        <span
          className={cn(
            'num text-h2 font-medium leading-none tabular-nums',
            free ? 'text-accent' : 'text-text',
          )}
        >
          {free ? '0.000' : animated.toFixed(3)}
        </span>
        {mechanismLabel ? (
          <span className="text-micro text-faint">{mechanismLabel}</span>
        ) : null}
      </div>
    </Tooltip>
  )
}
