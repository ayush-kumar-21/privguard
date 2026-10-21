import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'

import api, { apiError } from '../../lib/api'
import { qk } from '../../lib/queryKeys'
import type { PlanExplain } from '../../lib/types'
import { useDebounced } from '../../lib/useDebounced'
import { InlineError, Skeleton } from '../states'
import { Badge } from '../ui/badge'
import { Panel, PanelHeader } from '../ui/panel'

// react-flow is only fetched when a plan is actually drawn.
const PlanViewer = lazy(() => import('./PlanViewer'))

/**
 * The plan the engine would run for the SQL in the editor, from `EXPLAIN`. Asking costs no budget
 * and reads no data, so it refreshes as you type (debounced).
 */
export function PlanPanel({ sql }: { sql: string }) {
  const text = useDebounced(sql.trim(), 500)

  const plan = useQuery({
    queryKey: qk.explain(text),
    enabled: text.length > 0,
    retry: false,
    staleTime: 30_000,
    queryFn: async () => (await api.post<PlanExplain>('/dp/explain/', { sql: text })).data,
  })

  return (
    <Panel inset>
      <PanelHeader
        title="Query plan"
        description="What the engine will run. Tinted operators are the privacy operators; planning is free."
        actions={
          plan.data ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="neutral">
                <span className="num">{plan.data.mechanism.replace('_', ' ')}</span>
              </Badge>
              <Badge tone="neutral">
                <span className="num">sensitivity {plan.data.sensitivity.toFixed(2)}</span>
              </Badge>
              <Badge tone="outline">
                <span className="num">{plan.data.fingerprint.slice(0, 10)}</span>
              </Badge>
              {plan.data.cached ? <Badge tone="ok">cached: repeating is free</Badge> : null}
            </div>
          ) : null
        }
      />
      {plan.isError ? (
        <div className="p-5">
          <InlineError message={apiError(plan.error, 'No plan for this query.')} />
        </div>
      ) : plan.data ? (
        <Suspense fallback={<Skeleton className="m-5 h-[110px]" />}>
          <PlanViewer plan={plan.data} />
        </Suspense>
      ) : (
        <Skeleton className="m-5 h-[110px]" />
      )}
    </Panel>
  )
}
