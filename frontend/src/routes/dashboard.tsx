import {
  ArrowRight,
  Lightning,
  ShieldCheck,
  ShieldWarning,
  Terminal,
} from '@phosphor-icons/react'
import { Link } from 'react-router-dom'

import { BudgetBurndown } from '../components/charts/BudgetBurndown'
import { RhoMeter } from '../components/privacy/RhoMeter'
import { StatRow, StatTile } from '../components/privacy/StatTile'
import { StatusChip } from '../components/privacy/StatusChip'
import { EmptyState, ErrorState, Skeleton, SkeletonRows } from '../components/states'
import { Badge, Dot } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Panel, PanelHeader } from '../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../components/ui/table'
import { useBudget, useLedgerVerify } from '../hooks/useBudget'
import { useQueryCounts, useQueryHistory, useRecentQueries } from '../hooks/useQueryFeed'
import { epsFromRho } from '../lib/accounting'
import { budgetBand, eps, int, ms, oneLine, pct, relative, rho } from '../lib/format'
import { useAuth } from '../stores/auth'

export default function DashboardPage() {
  const principal = useAuth((state) => state.principal)
  const budget = useBudget()
  const ledger = useLedgerVerify()
  const counts = useQueryCounts()
  const history = useQueryHistory()
  const recent = useRecentQueries()

  const account = budget.data
  const band = account ? budgetBand(account.rho_remaining, account.rho_total) : 'ok'
  const cacheRate =
    counts.data && counts.data.total > 0
      ? counts.data.cache_hit / counts.data.total
      : null

  return (
    <div className="grid gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-display text-text">
            {principal ? `Welcome back, ${principal.username}` : 'Overview'}
          </h1>
          <p className="mt-1.5 max-w-[62ch] text-body leading-relaxed text-muted">
            Privacy budget, query history and ledger integrity for the
            <span className="num"> safehealth </span>
            database.
          </p>
        </div>
        <Button asChild variant="primary" size="md">
          <Link to="/console">
            <Terminal size={14} aria-hidden />
            Open console
          </Link>
        </Button>
      </header>

      {/* --- Stat tiles ---------------------------------------------------- */}
      <StatRow>
        <StatTile
          label="rho remaining"
          loading={budget.isPending}
          tone={account ? band : 'default'}
          value={account ? rho(account.rho_remaining) : '--'}
          unit={account ? `/ ${rho(account.rho_total, 2)}` : undefined}
          detail={
            account ? (
              <RhoMeter
                remaining={account.rho_remaining}
                total={account.rho_total}
                className="mt-1"
              />
            ) : (
              'No budget account. This principal does not run DP queries.'
            )
          }
        />
        <StatTile
          label="queries run"
          loading={counts.isPending}
          value={int(counts.data?.total ?? 0)}
          detail={
            counts.data
              ? `${int(counts.data.executed)} executed - ${int(counts.data.cache_hit)} from cache`
              : null
          }
        />
        <StatTile
          label="cache-hit rate"
          loading={counts.isPending}
          tone={cacheRate !== null && cacheRate > 0 ? 'accent' : 'default'}
          value={cacheRate === null ? '--' : pct(cacheRate)}
          detail="Memoised answers cost zero rho"
        />
        <StatTile
          label="ledger entries"
          loading={ledger.isPending}
          value={ledger.data ? int(ledger.data.entries) : '--'}
          detail={
            ledger.data ? (
              <span className="inline-flex items-center gap-1.5">
                {ledger.data.ok ? (
                  <>
                    <ShieldCheck size={13} weight="fill" className="text-ok" aria-hidden />
                    hash chain verified
                  </>
                ) : (
                  <>
                    <ShieldWarning
                      size={13}
                      weight="fill"
                      className="text-danger"
                      aria-hidden
                    />
                    broken at entry {ledger.data.first_bad_entry}
                  </>
                )}
              </span>
            ) : (
              'No ledger for this principal'
            )
          }
        />
      </StatRow>

      {/* --- Burn-down ----------------------------------------------------- */}
      <Panel inset>
        <PanelHeader
          title="Budget burn-down"
          description="Cumulative zCDP spend against the account ceiling. Composition is additive in rho, so this curve only ever rises."
          actions={
            account ? (
              <div className="flex items-center gap-2">
                <Badge tone="outline">
                  <span className="num">
                    eps ~ {eps(epsFromRho(account.rho_spent, account.delta_target ?? 1e-6), 2)}
                  </span>
                </Badge>
                <Badge tone={band === 'ok' ? 'ok' : band === 'warn' ? 'warn' : 'danger'}>
                  <Dot />
                  {account.status}
                </Badge>
              </div>
            ) : null
          }
        />

        <div className="p-5">
          {history.isPending || budget.isPending ? (
            <Skeleton className="h-[260px] w-full" />
          ) : history.isError ? (
            <ErrorState
              message="The query history could not be loaded."
              onRetry={() => void history.refetch()}
            />
          ) : !account ? (
            <EmptyState
              title="No budget account"
              description="Only principals with a DP budget accumulate a burn-down curve. Sign in as alice or frank to see one."
            />
          ) : (history.data?.rows.filter((row) => row.rho_charged > 0).length ?? 0) === 0 ? (
            <EmptyState
              icon={Lightning}
              title="No budget spent yet"
              description="Run a query in the console and the first charge appears here."
              action={
                <Button asChild variant="secondary" size="sm">
                  <Link to="/console">
                    Open the console
                    <ArrowRight size={13} aria-hidden />
                  </Link>
                </Button>
              }
            />
          ) : (
            <>
              <BudgetBurndown
                queries={history.data?.rows ?? []}
                rhoTotal={account.rho_total}
                delta={account.delta_target}
              />
              <p className="mt-3 text-micro text-faint">
                One axis, in rho. The epsilon equivalent is exact in the tooltip rather
                than plotted on a second scale.
                {history.data?.truncated
                  ? ' History is capped at the 200 most recent queries.'
                  : ''}
              </p>
            </>
          )}
        </div>
      </Panel>

      {/* --- Recent queries (the table twin of the chart above) ------------- */}
      <Panel inset>
        <PanelHeader
          title="Recent queries"
          description="Every answer the gateway returned, with what it cost."
          actions={
            <Button asChild variant="ghost" size="sm">
              <Link to="/governance/audit">
                View audit
                <ArrowRight size={13} aria-hidden />
              </Link>
            </Button>
          }
        />

        {recent.isError ? (
          <ErrorState
            message="The query feed could not be loaded."
            onRetry={() => void recent.refetch()}
          />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH>status</TH>
                  <TH>principal</TH>
                  <TH className="min-w-[22rem]">sql</TH>
                  <TH className="text-right">rho</TH>
                  <TH className="text-right">latency</TH>
                  <TH className="text-right">when</TH>
                </tr>
              </THead>
              <TBody>
                {recent.isPending ? (
                  <SkeletonRows
                    rows={6}
                    widths={['w-20', 'w-16', 'w-64', 'w-12', 'w-12', 'w-16']}
                  />
                ) : (recent.data?.length ?? 0) === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState
                        icon={Terminal}
                        title="No queries yet"
                        description="Open the console and ask SafeHealth something."
                        action={
                          <Button asChild variant="secondary" size="sm">
                            <Link to="/console">Open the console</Link>
                          </Button>
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  recent.data?.map((query) => (
                    <TR key={query.query_id}>
                      <TD>
                        <StatusChip status={query.status} />
                      </TD>
                      <TD className="num text-muted">{query.principal_name}</TD>
                      <TD>
                        <code
                          className="block max-w-[38rem] truncate text-micro text-muted"
                          title={query.sql_text}
                        >
                          {oneLine(query.sql_text)}
                        </code>
                      </TD>
                      <TD className="num text-right">{rho(query.rho_charged)}</TD>
                      <TD className="num text-right text-muted">{ms(query.latency_ms)}</TD>
                      <TD className="num text-right text-faint">
                        {relative(query.submitted_at)}
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
