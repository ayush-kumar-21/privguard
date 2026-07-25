import { useQuery } from '@tanstack/react-query'

import api from '../lib/api'
import { qk } from '../lib/queryKeys'
import type { Paginated, QueryAudit, QueryStatus } from '../lib/types'

/** DRF answers 20 rows per page; cap the walk so a busy account cannot stall the view. */
const MAX_PAGES = 10

/**
 * Walks the audit log oldest-first so the burn-down can accumulate rho in the
 * order it was actually spent. `truncated` is surfaced rather than hidden: a
 * partial history would otherwise silently understate the curve.
 */
export function useQueryHistory(enabled = true) {
  return useQuery({
    queryKey: qk.queries({ history: 1 }),
    enabled,
    queryFn: async () => {
      const rows: QueryAudit[] = []
      let page = 1
      let truncated = false

      for (;;) {
        const { data } = await api.get<Paginated<QueryAudit>>('/queries/', {
          params: { page, ordering: 'submitted_at' },
        })
        rows.push(...data.results)
        if (!data.next) break
        if (page >= MAX_PAGES) {
          truncated = true
          break
        }
        page += 1
      }
      return { rows, truncated }
    },
  })
}

/** Exact per-status totals, taken from the paginator `count` rather than a sample. */
export function useQueryCounts(enabled = true) {
  return useQuery({
    queryKey: qk.queries({ counts: 1 }),
    enabled,
    queryFn: async () => {
      const statuses: QueryStatus[] = [
        'executed',
        'cache_hit',
        'rejected_budget',
        'rejected_policy',
      ]
      const [all, ...perStatus] = await Promise.all([
        api.get<Paginated<QueryAudit>>('/queries/', { params: { page: 1 } }),
        ...statuses.map((status) =>
          api.get<Paginated<QueryAudit>>('/queries/', { params: { page: 1, status } }),
        ),
      ])

      const counts = Object.fromEntries(
        statuses.map((status, index) => [status, perStatus[index].data.count]),
      ) as Record<QueryStatus, number>

      return { total: all.data.count, ...counts }
    },
  })
}

/** Most recent activity for the dashboard feed. */
export function useRecentQueries(limit = 8) {
  return useQuery({
    queryKey: qk.queries({ recent: limit }),
    queryFn: async () => {
      const { data } = await api.get<Paginated<QueryAudit>>('/queries/', {
        params: { page: 1, ordering: '-submitted_at' },
      })
      return data.results.slice(0, limit)
    },
  })
}
