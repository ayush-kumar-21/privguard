import { useQuery } from '@tanstack/react-query'
import {
  ArrowClockwise,
  ShieldCheck,
  ShieldWarning,
  Vault,
} from '@phosphor-icons/react'

import { RhoMeter } from '../../components/privacy/RhoMeter'
import { EmptyState, ErrorState, SkeletonRows } from '../../components/states'
import { Badge, Dot } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { PageHeader, Panel, PanelHeader } from '../../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../../components/ui/table'
import { useLedgerVerify } from '../../hooks/useBudget'
import { apiError, fetchAll } from '../../lib/api'
import { epsFromRho } from '../../lib/accounting'
import { budgetBand, eps, int, rho } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import type { BudgetAccount } from '../../lib/types'

export default function LedgerPage() {
  const verify = useLedgerVerify()

  const accounts = useQuery({
    queryKey: qk.budgetAccounts(),
    queryFn: () => fetchAll<BudgetAccount>('/budget-accounts/'),
  })

  const ok = verify.data?.ok
  const hasLedger = Boolean(verify.data)

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Ledger"
        description="Every budget movement is a row in an append-only, hash-chained log. Each entry commits to its predecessor, so an edited or deleted row breaks the chain from that point forward."
      />

      {/* --- Integrity ----------------------------------------------------- */}
      <Panel
        className={
          hasLedger && ok === false
            ? 'border-danger/40 bg-danger-weak'
            : hasLedger && ok
              ? 'border-ok/30'
              : undefined
        }
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-start gap-4">
            {verify.isPending ? (
              <div className="h-9 w-9 shrink-0 animate-pulse rounded-full bg-surface-2" />
            ) : !hasLedger ? (
              // No account is not a failure, so it must not wear the tamper colour.
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface-2">
                <Vault size={18} className="text-faint" aria-hidden />
              </span>
            ) : ok ? (
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ok-weak">
                <ShieldCheck size={18} weight="fill" className="text-ok" aria-hidden />
              </span>
            ) : (
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-danger-weak">
                <ShieldWarning
                  size={18}
                  weight="fill"
                  className="text-danger"
                  aria-hidden
                />
              </span>
            )}

            <div className="min-w-0">
              <h2 className="text-h3 text-text">
                {verify.isPending
                  ? 'Verifying chain'
                  : !hasLedger
                    ? 'No ledger for this principal'
                    : ok
                      ? 'Chain intact'
                      : 'Chain broken'}
              </h2>
              <p className="mt-1 max-w-[62ch] text-small text-muted">
                {verify.isPending ? (
                  'Replaying every entry hash.'
                ) : !hasLedger ? (
                  'Only principals with a budget account keep a ledger. Sign in as alice, frank or erin.'
                ) : ok ? (
                  <>
                    Replayed{' '}
                    <span className="num text-text">{int(verify.data?.entries)}</span>{' '}
                    entries. Every hash matches the entry before it.
                  </>
                ) : (
                  <>
                    The chain diverges at entry{' '}
                    <span className="num text-danger">
                      {verify.data?.first_bad_entry}
                    </span>
                    . Everything from that row on is untrustworthy.
                  </>
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {hasLedger ? (
              <Badge tone={ok ? 'ok' : 'danger'}>
                <Dot />
                {ok ? 'verified' : 'tampered'}
              </Badge>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              loading={verify.isFetching}
              onClick={() => void verify.refetch()}
            >
              <ArrowClockwise size={13} weight="bold" aria-hidden />
              Verify again
            </Button>
          </div>
        </div>

        {verify.isError ? (
          <div className="mt-4">
            <ErrorState
              message={apiError(verify.error, 'The ledger could not be verified.')}
              onRetry={() => void verify.refetch()}
            />
          </div>
        ) : null}
      </Panel>

      {/* --- Accounts ------------------------------------------------------ */}
      <Panel inset>
        <PanelHeader
          title="Budget accounts"
          description="Read-only. Balances move only through the reserve and settle procedures, never by direct update."
        />

        {accounts.isError ? (
          <ErrorState
            message={apiError(accounts.error, 'Budget accounts could not be loaded.')}
            onRetry={() => void accounts.refetch()}
          />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH>principal</TH>
                  <TH className="min-w-[10rem]">remaining</TH>
                  <TH className="text-right">spent</TH>
                  <TH className="text-right">reserved</TH>
                  <TH className="text-right">total</TH>
                  <TH className="text-right">eps equivalent</TH>
                  <TH>status</TH>
                </tr>
              </THead>
              <TBody>
                {accounts.isPending ? (
                  <SkeletonRows
                    rows={4}
                    widths={['w-16', 'w-28', 'w-12', 'w-12', 'w-12', 'w-14', 'w-16']}
                  />
                ) : accounts.data.length === 0 ? (
                  <tr>
                    <td colSpan={7}>
                      <EmptyState
                        icon={Vault}
                        title="No budget accounts"
                        description="Run manage.py seed_demo to create accounts for alice, frank and erin."
                      />
                    </td>
                  </tr>
                ) : (
                  accounts.data.map((account) => {
                    const band = budgetBand(account.rho_remaining, account.rho_total)
                    return (
                      <TR key={account.account_id}>
                        <TD className="num">{account.principal_name}</TD>
                        <TD>
                          <div className="flex items-center gap-2.5">
                            <span
                              className={`num w-12 shrink-0 ${
                                band === 'ok'
                                  ? 'text-ok'
                                  : band === 'warn'
                                    ? 'text-warn'
                                    : 'text-danger'
                              }`}
                            >
                              {rho(account.rho_remaining)}
                            </span>
                            <RhoMeter
                              remaining={account.rho_remaining}
                              total={account.rho_total}
                              className="w-24"
                              height="h-1"
                              animate={false}
                            />
                          </div>
                        </TD>
                        <TD className="num text-right">{rho(account.rho_spent)}</TD>
                        <TD className="num text-right text-muted">
                          {rho(account.rho_reserved)}
                        </TD>
                        <TD className="num text-right text-muted">
                          {rho(account.rho_total, 2)}
                        </TD>
                        <TD className="num text-right text-muted">
                          {eps(
                            epsFromRho(account.rho_spent, account.delta_target ?? 1e-6),
                            2,
                          )}
                        </TD>
                        <TD>
                          <Badge
                            tone={
                              account.status === 'active'
                                ? 'ok'
                                : band === 'danger'
                                  ? 'danger'
                                  : 'neutral'
                            }
                          >
                            {account.status}
                          </Badge>
                        </TD>
                      </TR>
                    )
                  })
                )}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </Panel>
    </div>
  )
}
