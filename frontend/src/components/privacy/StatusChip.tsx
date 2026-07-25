import type { QueryStatus } from '../../lib/types'
import { Badge, Dot } from '../ui/badge'

type Tone = 'ok' | 'accent' | 'danger' | 'warn' | 'neutral'

const STATUS: Record<QueryStatus, { label: string; tone: Tone; hint: string }> = {
  executed: {
    label: 'executed',
    tone: 'ok',
    hint: 'Noise added and the budget was charged.',
  },
  cache_hit: {
    label: 'cache hit',
    tone: 'accent',
    hint: 'Memoised answer replayed. Charged 0 rho, which is what defeats averaging attacks.',
  },
  pending: {
    label: 'pending',
    tone: 'warn',
    hint: 'Budget reserved, execution not settled yet.',
  },
  rejected_budget: {
    label: 'rejected: budget',
    tone: 'danger',
    hint: 'The reservation failed. Not enough rho remaining.',
  },
  rejected_policy: {
    label: 'rejected: policy',
    tone: 'danger',
    hint: 'The query fell outside the supported subset or a column policy.',
  },
}

export function statusMeta(status: QueryStatus) {
  return STATUS[status] ?? { label: status, tone: 'neutral' as Tone, hint: '' }
}

export function StatusChip({ status }: { status: QueryStatus }) {
  const meta = statusMeta(status)
  return (
    <Badge tone={meta.tone} title={meta.hint}>
      <Dot />
      {meta.label}
    </Badge>
  )
}
