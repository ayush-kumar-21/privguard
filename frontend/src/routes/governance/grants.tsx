import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Info, ShieldSlash } from '@phosphor-icons/react'
import { useMemo } from 'react'
import { toast } from 'sonner'

import { EmptyState, ErrorState, Skeleton } from '../../components/states'
import { Badge } from '../../components/ui/badge'
import { Panel, PageHeader, PanelHeader } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../../components/ui/table'
import { Tooltip } from '../../components/ui/misc'
import api, { apiError, fetchAll } from '../../lib/api'
import { qk } from '../../lib/queryKeys'
import type { AccessMode, ColumnGrant, Role } from '../../lib/types'

const ACCESS_MODES: { value: AccessMode; label: string }[] = [
  { value: 'raw', label: 'raw' },
  { value: 'dp', label: 'dp' },
  { value: 'redacted', label: 'redacted' },
  { value: 'denied', label: 'denied' },
]

const MODE_TONE: Record<AccessMode, 'ok' | 'accent' | 'warn' | 'danger'> = {
  raw: 'ok',
  dp: 'accent',
  redacted: 'warn',
  denied: 'danger',
}

export default function GrantsPage() {
  const queryClient = useQueryClient()

  const roles = useQuery({
    queryKey: qk.roles(),
    queryFn: () => fetchAll<Role>('/roles/'),
  })

  // The matrix is only honest if it holds every grant, so this walks the
  // pages rather than rendering whatever fits on the first one.
  const grants = useQuery({
    queryKey: qk.columnGrants(),
    queryFn: () => fetchAll<ColumnGrant>('/column-grants/'),
  })

  /** column id -> role id -> grant, so the grid can be rendered as a matrix. */
  const matrix = useMemo(() => {
    const byColumn = new Map<number, Map<number, ColumnGrant>>()
    for (const grant of grants.data ?? []) {
      if (!byColumn.has(grant.column)) byColumn.set(grant.column, new Map())
      byColumn.get(grant.column)!.set(grant.role, grant)
    }
    return [...byColumn.entries()].sort((a, b) => a[0] - b[0])
  }, [grants.data])

  const setMode = useMutation({
    mutationFn: ({ grantId, mode }: { grantId: number; mode: AccessMode }) =>
      api.patch(`/column-grants/${grantId}/`, { access_mode: mode }),

    // Optimistic: the grid answers instantly, and rolls back if the API refuses.
    onMutate: async ({ grantId, mode }) => {
      await queryClient.cancelQueries({ queryKey: qk.columnGrants() })
      const previous = queryClient.getQueryData<ColumnGrant[]>(qk.columnGrants())
      queryClient.setQueryData<ColumnGrant[]>(qk.columnGrants(), (current) =>
        current?.map((grant) =>
          grant.grant_id === grantId ? { ...grant, access_mode: mode } : grant,
        ),
      )
      return { previous }
    },
    onError: (error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(qk.columnGrants(), context.previous)
      }
      toast.error('Grant not changed', {
        description: apiError(error, 'The API refused the update.'),
      })
    },
    onSuccess: () => toast.success('Access mode updated'),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.columnGrants() })
    },
  })

  const roleList = roles.data ?? []

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Column grants"
        description="Privacy as configuration. Each cell is the access mode one role has on one column: raw values, differentially private aggregates, redaction, or nothing at all."
      />

      <Panel inset>
        <PanelHeader
          title="Column access"
          description="column x role -> access mode"
          actions={
            <Tooltip content="The API exposes grants by column id. Human-readable column names need a catalog endpoint (GET /api/columns/) from the backend.">
              <span className="inline-flex cursor-help items-center gap-1.5 text-micro text-faint">
                <Info size={13} aria-hidden />
                columns shown by id
              </span>
            </Tooltip>
          }
        />

        {grants.isError || roles.isError ? (
          <ErrorState
            message={apiError(grants.error ?? roles.error, 'Grants could not be loaded.')}
            onRetry={() => {
              void grants.refetch()
              void roles.refetch()
            }}
          />
        ) : grants.isPending || roles.isPending ? (
          <div className="grid gap-2 p-5">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-9 w-full" />
            ))}
          </div>
        ) : matrix.length === 0 ? (
          <EmptyState
            icon={ShieldSlash}
            title="No column grants"
            description="Run manage.py seed_demo to create the demonstration grants."
          />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH className="sticky left-0 bg-surface">column</TH>
                  {roleList.map((role) => (
                    <TH key={role.role_id} className="min-w-[8.5rem]">
                      {role.role_name}
                    </TH>
                  ))}
                </tr>
              </THead>
              <TBody>
                {matrix.map(([columnId, byRole]) => (
                  <TR key={columnId}>
                    <TD className="num sticky left-0 bg-surface text-muted">
                      column #{columnId}
                    </TD>
                    {roleList.map((role) => {
                      const grant = byRole.get(role.role_id)
                      if (!grant) {
                        return (
                          <TD key={role.role_id}>
                            <span className="text-micro text-faint">no grant</span>
                          </TD>
                        )
                      }
                      return (
                        <TD key={role.role_id}>
                          <div className="flex items-center gap-2">
                            <Select
                              value={grant.access_mode}
                              onValueChange={(mode) =>
                                setMode.mutate({
                                  grantId: grant.grant_id,
                                  mode: mode as AccessMode,
                                })
                              }
                              options={ACCESS_MODES}
                              size="sm"
                              className="w-[7.5rem]"
                              aria-label={`Access mode for column ${columnId}, role ${role.role_name}`}
                            />
                            {grant.rho_multiplier !== 1 ? (
                              <Tooltip
                                content={`Role-specific noise scaling: this role's queries are charged ${grant.rho_multiplier}x rho on this column.`}
                              >
                                <Badge tone={MODE_TONE[grant.access_mode]}>
                                  <span className="num">{grant.rho_multiplier}x</span>
                                </Badge>
                              </Tooltip>
                            ) : null}
                          </div>
                        </TD>
                      )
                    })}
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </Panel>

      <Panel>
        <h2 className="text-h3">What the modes mean</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ['raw', 'The exact stored value. No noise, no budget charge.'],
            ['dp', 'Aggregates only, answered with calibrated noise and charged rho.'],
            ['redacted', 'A masked stand-in is returned in place of the value.'],
            ['denied', 'The column is invisible. Never serialised, never queryable.'],
          ].map(([mode, meaning]) => (
            <div key={mode} className="rounded-sm border border-border px-3 py-2.5">
              <dt>
                <Badge tone={MODE_TONE[mode as AccessMode]}>{mode}</Badge>
              </dt>
              <dd className="mt-2 text-micro leading-relaxed text-muted">{meaning}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </div>
  )
}
