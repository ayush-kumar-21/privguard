import { lazy, useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AppShell } from './components/app-shell/AppShell'
import { Mark } from './components/app-shell/Wordmark'
import { RouteGate } from './components/states/RoleGate'
import { GATEWAY_MODE } from './lib/backend'
import { useAuth } from './stores/auth'
import LoginPage from './routes/login'
import NotFoundPage from './routes/not-found'

// Routes are split so CodeMirror and Recharts are fetched only by the screens
// that actually draw with them, which keeps the first paint small.
const DashboardPage = lazy(() => import('./routes/dashboard'))
const ConsolePage = lazy(() => import('./routes/console'))
const AttackLabPage = lazy(() => import('./routes/attack-lab'))
const PatientsPage = lazy(() => import('./routes/data/patients'))
const VisitsPage = lazy(() => import('./routes/data/visits'))
const DiagnosesPage = lazy(() => import('./routes/data/diagnoses'))
const LabsPage = lazy(() => import('./routes/data/labs'))
const PoliciesPage = lazy(() => import('./routes/governance/policies'))
const GrantsPage = lazy(() => import('./routes/governance/grants'))
const RolesPage = lazy(() => import('./routes/governance/roles'))
const UsersPage = lazy(() => import('./routes/governance/users'))
const PolicyGridPage = lazy(() => import('./routes/governance/grid'))
const AuditPage = lazy(() => import('./routes/governance/audit'))
const LedgerPage = lazy(() => import('./routes/governance/ledger'))

/** Held while the refresh token is exchanged, so routes never flash to login. */
function BootSplash() {
  return (
    <div className="grid min-h-dvh place-items-center bg-bg">
      <div className="flex items-center gap-3 text-muted">
        <Mark size={18} />
        <span className="headline text-h3">PrivGuard</span>
      </div>
    </div>
  )
}

function RequireAuth({ children }: { children: ReactNode }) {
  const principal = useAuth((state) => state.principal)
  const booting = useAuth((state) => state.booting)
  const location = useLocation()

  if (booting) return <BootSplash />
  if (!principal) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

export default function App() {
  const hydrate = useAuth((state) => state.hydrate)

  useEffect(() => {
    void hydrate()
  }, [hydrate])

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route
          path="console"
          element={
            <RouteGate roles={['dp_analyst', 'auditor']}>
              <ConsolePage />
            </RouteGate>
          }
        />

        {!GATEWAY_MODE && (
          <Route path="data">
            <Route index element={<Navigate to="/data/patients" replace />} />
            <Route
              path="patients"
              element={
                <RouteGate roles={['clinician']}>
                  <PatientsPage />
                </RouteGate>
              }
            />
            <Route
              path="visits"
              element={
                <RouteGate roles={['clinician']}>
                  <VisitsPage />
                </RouteGate>
              }
            />
            <Route
              path="diagnoses"
              element={
                <RouteGate roles={['clinician']}>
                  <DiagnosesPage />
                </RouteGate>
              }
            />
            <Route
              path="labs"
              element={
                <RouteGate roles={['clinician']}>
                  <LabsPage />
                </RouteGate>
              }
            />
          </Route>
        )}

        <Route path="governance">
          <Route index element={<Navigate to="/governance/audit" replace />} />
          {!GATEWAY_MODE && (
            <>
              <Route
                path="policies"
                element={
                  <RouteGate roles={['dba']}>
                    <PoliciesPage />
                  </RouteGate>
                }
              />
              <Route
                path="grants"
                element={
                  <RouteGate roles={['dba']}>
                    <GrantsPage />
                  </RouteGate>
                }
              />
              <Route
                path="roles"
                element={
                  <RouteGate roles={['dba']}>
                    <RolesPage />
                  </RouteGate>
                }
              />
              <Route
                path="users"
                element={
                  <RouteGate roles={['dba']}>
                    <UsersPage />
                  </RouteGate>
                }
              />
            </>
          )}
          {GATEWAY_MODE && (
            <Route
              path="grid"
              element={
                <RouteGate roles={['dba', 'auditor']}>
                  <PolicyGridPage />
                </RouteGate>
              }
            />
          )}
          <Route
            path="audit"
            element={
              <RouteGate roles={['auditor', 'dp_analyst']}>
                <AuditPage />
              </RouteGate>
            }
          />
          <Route
            path="ledger"
            element={
              <RouteGate roles={['auditor']}>
                <LedgerPage />
              </RouteGate>
            }
          />
        </Route>

        <Route
          path="attack-lab"
          element={
            <RouteGate roles={['dp_analyst', 'auditor']}>
              <AttackLabPage />
            </RouteGate>
          }
        />

        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
