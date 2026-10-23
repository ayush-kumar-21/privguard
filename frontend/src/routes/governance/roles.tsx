import { useQuery } from '@tanstack/react-query'
import { IdentificationBadge } from '@phosphor-icons/react'

import { EmptyState, ErrorState, SkeletonRows } from '../../components/states'
import { Badge } from '../../components/ui/badge'
import { PageHeader, Panel, PanelHeader } from '../../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../../components/ui/table'
import api, { apiError } from '../../lib/api'
import { qk } from '../../lib/queryKeys'
import type { Paginated, Role } from '../../lib/types'

/** What each seeded role can reach, mirroring api/permissions.py and the nav. */
const ROLE_REACH: Record<string, string[]> = {
  dp_analyst: ['Overview', 'Console', 'Audit', 'Attack Lab'],
  clinician: ['Overview', 'Patients', 'Visits', 'Diagnoses', 'Lab results'],
  auditor: ['Overview', 'Console', 'Audit', 'Ledger', 'Attack Lab'],
  dba: ['Everything, including policy administration'],
}
// Job roles (researcher, nurse, ...) carry one of the four roles above and reach exactly what it does.

export default function RolesPage() {
  const roles = useQuery({
    queryKey: qk.roles(),
    queryFn: async () => {
      const { data } = await api.get<Paginated<Role> | Role[]>('/roles/')
      return Array.isArray(data) ? data : data.results
    },
  })

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Roles"
        description="Roles are resolved through the principal-role association, not through per-role tables. A principal's roles decide both the nav they see and the columns the API will serialise for them."
      />

      <Panel inset>
        <PanelHeader title="Defined roles" description="from sys_role" />

        {roles.isError ? (
          <ErrorState
            message={apiError(roles.error, 'Roles could not be loaded.')}
            onRetry={() => void roles.refetch()}
          />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH>role</TH>
                  <TH>description</TH>
                  <TH>reaches</TH>
                </tr>
              </THead>
              <TBody>
                {roles.isPending ? (
                  <SkeletonRows rows={4} widths={['w-24', 'w-56', 'w-64']} />
                ) : roles.data.length === 0 ? (
                  <tr>
                    <td colSpan={3}>
                      <EmptyState
                        icon={IdentificationBadge}
                        title="No roles defined"
                        description="Run manage.py migrate to create the standard roles."
                      />
                    </td>
                  </tr>
                ) : (
                  roles.data.map((role) => (
                    <TR key={role.role_id}>
                      <TD>
                        <Badge tone={role.role_name === 'dba' ? 'accent' : 'outline'}>
                          <span className="num">{role.role_name}</span>
                        </Badge>
                      </TD>
                      <TD className="text-muted">
                        {role.description ?? '--'}
                        {role.carries ? (
                          <span className="num ml-1.5 text-faint">(carries {role.carries})</span>
                        ) : null}
                      </TD>
                      <TD>
                        <div className="flex flex-wrap gap-1">
                          {(ROLE_REACH[role.carries ?? role.role_name] ?? ['--']).map((surface) => (
                            <span
                              key={surface}
                              className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-micro text-muted"
                            >
                              {surface}
                            </span>
                          ))}
                        </div>
                      </TD>
                    </TR>
                  ))
                )}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </Panel>
    </div>
  )
}
