import { Lock } from '@phosphor-icons/react'
import type { ReactNode } from 'react'

import { useAuth } from '../../stores/auth'
import type { RoleName } from '../../lib/types'
import { EmptyState } from './index'

/**
 * Renders children only when the principal holds one of `roles`. The dba role
 * passes every gate. This mirrors the API permissions rather than replacing
 * them: the server is still the authority on every 403.
 */
export function RoleGate({
  roles,
  children,
  fallback = null,
}: {
  roles: RoleName[]
  children: ReactNode
  fallback?: ReactNode
}) {
  const allowed = useAuth((state) => state.hasRole(...roles))
  return <>{allowed ? children : fallback}</>
}

/** Full-page variant used by route guards, so a deep link explains itself. */
export function RouteGate({
  roles,
  children,
}: {
  roles: RoleName[]
  children: ReactNode
}) {
  return (
    <RoleGate
      roles={roles}
      fallback={
        <EmptyState
          icon={Lock}
          title="Not available for your role"
          description={`This surface is restricted to ${roles.join(', ')}. Ask a DBA if you need access.`}
        />
      }
    >
      {children}
    </RoleGate>
  )
}
