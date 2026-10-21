import { ShieldSlash } from '@phosphor-icons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { EmptyState, ErrorState, Skeleton } from '../../components/states'
import { Badge } from '../../components/ui/badge'
import { Switch, Tooltip } from '../../components/ui/misc'
import { PageHeader, Panel, PanelHeader } from '../../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../../components/ui/table'
import api, { apiError } from '../../lib/api'
import { qk } from '../../lib/queryKeys'
import type { PolicyColumn, PolicyGrid } from '../../lib/types'
import { useAuth } from '../../stores/auth'

const ACCESS_TONE: Record<PolicyColumn['access'], 'ok' | 'accent' | 'danger'> = {
  allowed: 'ok',
  aggregate_only: 'accent',
  denied: 'danger',
}

interface GrantChange {
  role: string
  table: string
  column: string
  granted: boolean
}

/**
 * The column x role grid, read from the engine's own catalog (`sys_column_privacy`,
 * `sys_column_grant`). A cell is a grant: whether that role's principals may reference the column
 * in a query at all, as an aggregate, a filter or a group key. Only a dba can flip one.
 */
export default function PolicyGridPage() {
  const queryClient = useQueryClient()
  const canEdit = useAuth((state) => state.principal?.roles.includes('dba') ?? false)

  const grid = useQuery({
    queryKey: qk.policyGrid(),
    queryFn: async () => (await api.get<PolicyGrid>('/policy/grid/')).data,
  })

  const toggle = useMutation({
    mutationFn: async (change: GrantChange) =>
      (await api.put<PolicyGrid>('/policy/grants/', change)).data,
    // Optimistic: the switch moves at once and snaps back if the engine refuses.
    onMutate: async (change) => {
      await queryClient.cancelQueries({ queryKey: qk.policyGrid() })
      const previous = queryClient.getQueryData<PolicyGrid>(qk.policyGrid())
      queryClient.setQueryData<PolicyGrid>(qk.policyGrid(), (current) =>
        current
          ? {
              ...current,
              columns: current.columns.map((c) =>
                c.table === change.table && c.column === change.column
                  ? { ...c, grants: { ...c.grants, [change.role]: change.granted } }
                  : c,
              ),
            }
          : current,
      )
      return { previous }
    },
    onError: (error, _change, context) => {
      if (context?.previous) queryClient.setQueryData(qk.policyGrid(), context.previous)
      toast.error('Grant not changed', {
        description: apiError(error, 'The engine refused the change.'),
      })
    },
    onSuccess: (data, change) => {
      queryClient.setQueryData(qk.policyGrid(), data)
      toast.success(
        change.granted
          ? `${change.role} can now reference ${change.column}`
          : `${change.role} can no longer reference ${change.column}`,
      )
    },
  })

  const roles = grid.data?.roles ?? []
  const columns = grid.data?.columns ?? []

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Policy grid"
        description="Privacy as configuration. Each switch lets one role reference one column of the fact table in a query: as the aggregated value, a filter or a group key. A query that touches an ungranted column is refused before it costs any budget."
      />

      <Panel inset>
        <PanelHeader
          title="Column access"
          description="column x role, from the engine catalog"
          actions={
            canEdit ? null : (
              <Badge tone="neutral">read only</Badge>
            )
          }
        />

        {grid.isError ? (
          <ErrorState
            message={apiError(grid.error, 'The policy grid could not be loaded.')}
            onRetry={() => void grid.refetch()}
          />
        ) : grid.isPending ? (
          <div className="grid gap-2 p-5">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-9 w-full" />
            ))}
          </div>
        ) : columns.length === 0 ? (
          <EmptyState
            icon={ShieldSlash}
            title="No columns"
            description="The catalog has no privacy-unit fact table yet."
          />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH className="sticky left-0 bg-surface">column</TH>
                  <TH>type</TH>
                  <TH>access</TH>
                  <TH>aggregate policy</TH>
                  {roles.map((role) => (
                    <TH key={role} className="min-w-[7rem] text-center">
                      {role}
                    </TH>
                  ))}
                </tr>
              </THead>
              <TBody>
                {columns.map((c) => {
                  const denied = c.access === 'denied'
                  return (
                    <TR key={`${c.table}.${c.column}`}>
                      <TD className="sticky left-0 bg-surface">
                        <span className="num text-text">{c.column}</span>
                        <span className="ml-2 inline-flex gap-1">
                          {c.filterable ? <Badge tone="outline">filter</Badge> : null}
                          {c.groupable ? <Badge tone="outline">group</Badge> : null}
                        </span>
                      </TD>
                      <TD className="num text-muted">{c.type}</TD>
                      <TD>
                        <Badge tone={ACCESS_TONE[c.access]}>{c.access.replace('_', ' ')}</Badge>
                      </TD>
                      <TD className="num text-muted">
                        {c.lower_clamp !== null && c.upper_clamp !== null
                          ? `${c.mechanism ?? 'laplace'} [${c.lower_clamp}, ${c.upper_clamp}]`
                          : '-'}
                      </TD>
                      {roles.map((role) => (
                        <TD key={role} className="text-center">
                          <Tooltip
                            content={
                              denied
                                ? 'Denied columns are refused for everyone, whatever the grants.'
                                : canEdit
                                  ? `Toggle ${role} on ${c.column}`
                                  : 'Only a dba can change grants.'
                            }
                          >
                            <span className="inline-flex">
                              <Switch
                                checked={c.grants[role] ?? false}
                                disabled={!canEdit || denied || toggle.isPending}
                                onCheckedChange={(granted) =>
                                  toggle.mutate({
                                    role,
                                    table: c.table,
                                    column: c.column,
                                    granted,
                                  })
                                }
                                aria-label={`${role} may reference ${c.column}`}
                              />
                            </span>
                          </Tooltip>
                        </TD>
                      ))}
                    </TR>
                  )
                })}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </Panel>
    </div>
  )
}
