import { useAuth } from '../../stores/auth'
import type { RoleName } from '../../lib/types'
import { NAV } from './nav'

/**
 * What each role is *for*, in one line. The sidebar otherwise leaves a large
 * dead area below the nav for roles that see few surfaces, and filling it with
 * the caller's own authority is more useful than padding: role-based menu
 * access is invisible when you only ever sign in as one principal.
 */
const ROLE_BLURB: Record<RoleName, string> = {
  dp_analyst: 'Queries through the DP gateway. Spends a privacy budget.',
  clinician: 'Reads identified records. No aggregate query access.',
  auditor: 'Reads the query log and the budget ledger.',
  dba: 'Every surface, including policy administration.',
}

const ROLE_LABEL: Record<RoleName, string> = {
  dp_analyst: 'analyst',
  clinician: 'clinician',
  auditor: 'auditor',
  dba: 'dba',
}

const TOTAL_SURFACES = NAV.reduce((n, group) => n + group.items.length, 0)

export function RoleCard() {
  const principal = useAuth((state) => state.principal)
  const hasRole = useAuth((state) => state.hasRole)

  if (!principal || principal.roles.length === 0) return null

  const visible = NAV.reduce(
    (n, group) => n + group.items.filter((item) => hasRole(...item.roles)).length,
    0,
  )

  // Describe the strongest base role the principal holds (job roles carry one). dba passes
  // every gate, so it wins when a principal holds several.
  const primary: RoleName =
    (['dba', 'auditor', 'dp_analyst', 'clinician'] as const).find((r) =>
      principal.roles.includes(r),
    ) ?? 'dp_analyst'

  return (
    <div className="mx-3 mb-3 rounded-md border border-border bg-surface-2 px-3 py-2.5">
      <p className="label-micro pb-1.5">Signed in as</p>

      <div className="flex flex-wrap items-center gap-1.5">
        {(principal.assigned_roles ?? principal.roles).map((role) => (
          <span
            key={role}
            className="num rounded-sm bg-accent-weak px-1.5 py-0.5 text-micro font-medium text-accent"
          >
            {ROLE_LABEL[role as RoleName] ?? role.replace(/_/g, ' ')}
          </span>
        ))}
      </div>

      <p className="mt-2 text-micro leading-snug text-muted">{ROLE_BLURB[primary]}</p>

      <p className="num mt-2 text-micro text-faint">
        {visible} of {TOTAL_SURFACES} surfaces
      </p>
    </div>
  )
}
