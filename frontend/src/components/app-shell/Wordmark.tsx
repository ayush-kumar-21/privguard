import { cn } from '../../lib/cn'

/**
 * The mark is a 2x2 block of data where one cell has been withheld: the whole
 * product in one glyph. Drawn in CSS, not an SVG icon, so it tracks the accent
 * token through a theme change without a second asset.
 */
export function Mark({ size = 18, className }: { size?: number; className?: string }) {
  const unit = (size - 2) / 2
  const radius = Math.max(2, Math.round(size / 9))
  const cell = { width: unit, height: unit, borderRadius: radius }

  return (
    <span
      aria-hidden
      className={cn('grid shrink-0 grid-cols-2 gap-[2px]', className)}
      style={{ width: size, height: size }}
    >
      <span className="bg-accent" style={cell} />
      <span className="bg-accent/70" style={cell} />
      <span className="bg-accent/70" style={cell} />
      {/* the redacted quadrant */}
      <span
        className="border border-dashed border-accent/70 bg-accent/10"
        style={cell}
      />
    </span>
  )
}

export function Wordmark({
  compact = false,
  size = 18,
  className,
}: {
  compact?: boolean
  size?: number
  className?: string
}) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <Mark size={size} />
      {compact ? null : (
        <span className="headline text-[16px] tracking-tight text-text">
          PrivGuard
        </span>
      )}
    </span>
  )
}
