import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Info,
  Lightning,
  ListMagnifyingGlass,
  Play,
  Warning,
} from '@phosphor-icons/react'
import { useMemo, useState } from 'react'

import { PlanPanel } from '../components/console/PlanPanel'
import { SqlEditor } from '../components/console/SqlEditor'
import { RhoBadge } from '../components/privacy/RhoBadge'
import { RhoMeter } from '../components/privacy/RhoMeter'
import { StatusChip } from '../components/privacy/StatusChip'
import { EmptyState, InlineError } from '../components/states'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Kbd, Tooltip } from '../components/ui/misc'
import { Panel, PanelHeader } from '../components/ui/panel'
import { Select } from '../components/ui/select'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../components/ui/table'
import { useBudget } from '../hooks/useBudget'
import api, { apiError } from '../lib/api'
import { GATEWAY_MODE } from '../lib/backend'
import { qk } from '../lib/queryKeys'
import { eps, metric, ms, rho as fmtRho } from '../lib/format'
import type { DPQueryResponse } from '../lib/types'

const RHO_OPTIONS = [
  { value: '0.01', label: 'rho 0.01  (noisier, cheap)' },
  { value: '0.05', label: 'rho 0.05  (default)' },
  { value: '0.1', label: 'rho 0.10  (tighter)' },
  { value: '0.25', label: 'rho 0.25  (expensive)' },
]

const TEMPLATES = [
  {
    label: 'Count all visits',
    sql: 'SELECT COUNT(*) FROM visits',
  },
  {
    label: 'Count by admission type',
    sql: 'SELECT admission_type, COUNT(*)\nFROM visits\nGROUP BY admission_type',
  },
  {
    label: 'Emergency charges',
    sql: "SELECT SUM(total_charge)\nFROM visits\nWHERE admission_type = 'EMERGENCY'",
  },
  {
    label: 'Average charge',
    sql: 'SELECT AVG(total_charge) FROM visits',
  },
]

/**
 * Best-effort read of which mechanism the gateway will pick, so the cost panel
 * says something useful before the round trip. The server decides for real;
 * once a result comes back the badge shows the mechanism it actually used.
 */
function predictMechanism(sql: string): string {
  const text = sql.toUpperCase()
  if (/GROUP\s+BY/.test(text)) return 'gaussian'
  if (/COUNT\s*\(/.test(text)) return 'discrete_laplace'
  if (/SUM\s*\(|AVG\s*\(/.test(text)) return 'laplace'
  return 'laplace'
}

export default function ConsolePage() {
  const [sqlText, setSqlText] = useState(TEMPLATES[1].sql)
  const [rhoRequest, setRhoRequest] = useState('0.05')
  const queryClient = useQueryClient()
  const budget = useBudget()

  const run = useMutation({
    mutationFn: async () => {
      // The gateway records server-side latency in the audit log but does not
      // return it, so the console reports the round trip it can actually see.
      const started = performance.now()
      const { data } = await api.post<DPQueryResponse>('/dp/query/', {
        db: 'safehealth',
        sql: sqlText,
        rho: Number(rhoRequest),
      })
      return { ...data, roundTripMs: performance.now() - started }
    },
    onSuccess: () => {
      // The charge changed the account and the audit log; refetch both.
      void queryClient.invalidateQueries({ queryKey: qk.budget() })
      void queryClient.invalidateQueries({ queryKey: ['queries'] })
      void queryClient.invalidateQueries({ queryKey: qk.ledgerVerify() })
    },
  })

  const result = run.data
  const columns = useMemo(() => {
    if (!result?.result?.length) return []
    const keys = new Set<string>()
    for (const row of result.result) {
      for (const key of Object.keys(row)) keys.add(key)
    }
    // `value` is the aggregate, always last so the group key reads first.
    return [...keys].sort((a, b) => (a === 'value' ? 1 : b === 'value' ? -1 : 0))
  }, [result])

  const canRun = sqlText.trim().length > 0 && !run.isPending
  const account = budget.data

  return (
    <div className="grid gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-display text-text">SQL console</h1>
          <p className="mt-1.5 max-w-[64ch] text-body leading-relaxed text-muted">
            Aggregates over
            <span className="num"> visits</span>.{' '}
            {GATEWAY_MODE
              ? 'Your SQL is bound against the engine catalog and executed by the engine itself; it is never re-emitted as SQL.'
              : 'Your SQL is parsed into a validated spec and replayed through the ORM, so it never reaches the database as text.'}
          </p>
        </div>
      </header>

      {/* --- Editor + cost ------------------------------------------------- */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <Panel inset className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
            <span className="label-micro mr-1">templates</span>
            {TEMPLATES.map((template) => (
              <Button
                key={template.label}
                size="sm"
                variant="ghost"
                onClick={() => setSqlText(template.sql)}
              >
                {template.label}
              </Button>
            ))}
          </div>
          <SqlEditor
            value={sqlText}
            onChange={setSqlText}
            onRun={() => canRun && run.mutate()}
          />
        </Panel>

        <div className="grid content-start gap-3">
          <RhoBadge
            rho={Number(rhoRequest)}
            mechanism={result?.mechanism ?? predictMechanism(sqlText)}
            label={result ? 'rho charged' : 'rho cost (estimate)'}
            free={result?.status === 'cache_hit'}
            className="w-full"
          />

          <div className="grid gap-1.5">
            <label htmlFor="rho-request" className="label-micro">
              budget to spend
            </label>
            <Select
              value={rhoRequest}
              onValueChange={setRhoRequest}
              options={RHO_OPTIONS}
              aria-label="Budget to spend"
            />
          </div>

          <Button
            variant="primary"
            size="lg"
            onClick={() => run.mutate()}
            disabled={!canRun}
            loading={run.isPending}
            className="w-full"
          >
            {run.isPending ? 'Running' : 'Run query'}
            {run.isPending ? null : <Play size={14} weight="fill" aria-hidden />}
          </Button>

          <p className="flex items-center justify-center gap-1.5 text-micro text-faint">
            <Kbd>Ctrl</Kbd>
            <Kbd>Enter</Kbd>
            to run
          </p>

          {account ? (
            <div className="mt-1 grid gap-1.5 rounded-sm border border-border bg-surface px-3 py-2.5">
              <div className="flex items-baseline justify-between">
                <span className="label-micro">remaining</span>
                <span className="num text-small text-text">
                  {fmtRho(account.rho_remaining)}
                </span>
              </div>
              <RhoMeter remaining={account.rho_remaining} total={account.rho_total} />
            </div>
          ) : null}
        </div>
      </div>

      {GATEWAY_MODE ? <PlanPanel sql={sqlText} /> : null}

      {/* --- Result -------------------------------------------------------- */}
      {run.isError ? (
        <InlineError
          message={
            <>
              <span className="font-medium">Query rejected. </span>
              {apiError(run.error, 'The gateway refused this query.')}
            </>
          }
        />
      ) : null}

      <Panel inset>
        <PanelHeader
          title="Result"
          description={
            result
              ? 'Every figure below is noisy. Treat it as an estimate with the stated privacy cost.'
              : 'Run a query to see a differentially private answer.'
          }
          actions={
            result ? (
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip status={result.status} />
                <Badge tone="neutral">
                  <span className="num">rho {fmtRho(result.rho_charged)}</span>
                </Badge>
                <Badge tone="neutral">
                  <span className="num">eps {eps(result.epsilon_charged, 2)}</span>
                </Badge>
                <Badge tone="outline">
                  <span className="num">#{result.query_id}</span>
                </Badge>
              </div>
            ) : null
          }
        />

        {!result ? (
          <EmptyState
            icon={ListMagnifyingGlass}
            title="No result yet"
            description="Pick a template or write an aggregate over visits, then run it."
          />
        ) : result.status.startsWith('rejected') ? (
          <div className="p-5">
            <InlineError
              message={
                result.status === 'rejected_budget'
                  ? 'Budget reservation failed. This account does not have enough rho left for the requested spend.'
                  : 'The query was refused by a column or table policy.'
              }
            />
          </div>
        ) : result.result.length === 0 ? (
          <EmptyState
            icon={Warning}
            title="No rows"
            description="Either the predicate matched nothing, or every partition fell below the DP selection threshold and was suppressed."
          />
        ) : (
          <>
            <TableScroll>
              <Table>
                <THead>
                  <tr>
                    {columns.map((column) => (
                      <TH
                        key={column}
                        className={column === 'value' ? 'text-right' : undefined}
                      >
                        {column === 'value' ? 'noisy value' : column}
                      </TH>
                    ))}
                  </tr>
                </THead>
                <TBody>
                  {result.result.map((row, index) => (
                    <TR key={index}>
                      {columns.map((column) => (
                        <TD
                          key={column}
                          className={
                            column === 'value' ? 'num text-right font-medium' : 'text-text'
                          }
                        >
                          {metric(row[column])}
                        </TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableScroll>

            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border px-5 py-3 text-micro text-muted">
              <span className="inline-flex items-center gap-1.5">
                <Lightning size={12} weight="fill" className="text-faint" aria-hidden />
                round trip <span className="num text-text">{ms(result.roundTripMs)}</span>
              </span>
              <span>
                mechanism <span className="num text-text">{result.mechanism}</span>
              </span>
              {result.suppressed_groups !== undefined ? (
                <Tooltip content="Groups whose distinct-contributor count fell below the DP partition-selection threshold. Dropping them is what stops a rare group from outing one person.">
                  <span className="inline-flex cursor-help items-center gap-1.5">
                    <Info size={12} className="text-faint" aria-hidden />
                    suppressed groups{' '}
                    <span className="num text-text">{result.suppressed_groups}</span>
                  </span>
                </Tooltip>
              ) : null}
              <span className="ml-auto">
                budget after{' '}
                <span className="num text-text">
                  {fmtRho(result.budget.rho_remaining)} / {fmtRho(result.budget.rho_total, 2)}
                </span>
              </span>
            </div>
          </>
        )}
      </Panel>
    </div>
  )
}
