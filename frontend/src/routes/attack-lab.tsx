import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Flask, Info, Play, Repeat, Subtract } from '@phosphor-icons/react'
import { useMemo, useState } from 'react'

import {
  AveragingChart,
  type AveragingPoint,
} from '../components/charts/AveragingChart'
import { RhoMeter } from '../components/privacy/RhoMeter'
import { StatusChip } from '../components/privacy/StatusChip'
import { EmptyState, InlineError } from '../components/states'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Tooltip } from '../components/ui/misc'
import { PageHeader, Panel, PanelHeader } from '../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../components/ui/table'
import { useBudget } from '../hooks/useBudget'
import api, { apiError } from '../lib/api'
import { laplaceScaleFromRho } from '../lib/accounting'
import { metric, pct, rho as fmtRho } from '../lib/format'
import { qk } from '../lib/queryKeys'
import type { DPQueryResponse } from '../lib/types'

const RUNS = 24
const ATTACK_RHO = 0.05
/** The contribution bound C from the seeded visits table privacy policy. */
const SENSITIVITY = 5

const AVERAGING_SQL = "SELECT COUNT(*) FROM visits WHERE admission_type = 'EMERGENCY'"

const DIFFERENCING_A = 'SELECT COUNT(*) FROM visits WHERE total_charge > 100'
const DIFFERENCING_B =
  "SELECT COUNT(*) FROM visits WHERE total_charge > 100 AND admission_type != 'EMERGENCY'"

interface Attempt {
  index: number
  status: DPQueryResponse['status']
  value: number | null
  rhoCharged: number
}

function firstValue(response: DPQueryResponse): number | null {
  const row = response.result?.[0]
  if (!row) return null
  const value = row.value
  return typeof value === 'number' ? value : Number(value)
}

// ---------------------------------------------------------------------------
// Averaging attack
// ---------------------------------------------------------------------------
function AveragingPanel() {
  const queryClient = useQueryClient()
  const [attempts, setAttempts] = useState<Attempt[]>([])

  const run = useMutation({
    mutationFn: async () => {
      const collected: Attempt[] = []
      for (let index = 1; index <= RUNS; index += 1) {
        const { data } = await api.post<DPQueryResponse>('/dp/query/', {
          db: 'safehealth',
          sql: AVERAGING_SQL,
          rho: ATTACK_RHO,
        })
        collected.push({
          index,
          status: data.status,
          value: firstValue(data),
          rhoCharged: data.rho_charged,
        })
        setAttempts([...collected])
        if (data.status.startsWith('rejected')) break
      }
      return collected
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.budget() })
      void queryClient.invalidateQueries({ queryKey: ['queries'] })
    },
  })

  const answers = attempts.map((attempt) => attempt.value).filter((v): v is number => v !== null)
  const distinct = new Set(answers.map((value) => value.toFixed(4)))
  const totalRho = attempts.reduce((sum, attempt) => sum + attempt.rhoCharged, 0)
  const cacheHits = attempts.filter((attempt) => attempt.status === 'cache_hit').length

  /**
   * The attacker's standard error. A Laplace draw at scale b has sd b*sqrt(2);
   * averaging k independent draws divides that by sqrt(k). Memoisation means
   * k never exceeds 1, so the curve is flat by construction.
   */
  const curve = useMemo<AveragingPoint[]>(() => {
    const b = laplaceScaleFromRho(SENSITIVITY, ATTACK_RHO)
    const single = b * Math.SQRT2
    return Array.from({ length: RUNS }, (_, i) => {
      const k = i + 1
      return {
        k,
        independent: Number((single / Math.sqrt(k)).toFixed(4)),
        memoised: Number(single.toFixed(4)),
      }
    })
  }, [])

  return (
    <Panel inset>
      <PanelHeader
        title="Averaging attack"
        description="Ask the same question over and over and average the answers away."
        actions={
          <Button
            variant="primary"
            size="sm"
            loading={run.isPending}
            onClick={() => {
              setAttempts([])
              run.mutate()
            }}
          >
            {run.isPending ? `Running ${attempts.length}/${RUNS}` : `Run ${RUNS} times`}
            {run.isPending ? null : <Repeat size={13} weight="bold" aria-hidden />}
          </Button>
        }
      />

      <div className="grid gap-5 p-5">
        <p className="max-w-[70ch] text-small leading-relaxed text-muted">
          Independent noise is zero-mean, so an attacker who can re-ask a question
          <span className="num"> k </span>
          times shrinks their error by
          <span className="num"> sqrt(k)</span>. PrivGuard fingerprints the parsed
          query and replays the first answer from the noise cache, charging
          <span className="num"> rho 0 </span>
          for every repeat. The attacker gets the same number back forever.
        </p>

        <AveragingChart data={curve} />

        <p className="text-micro text-faint">
          The falling curve is the textbook attack, computed from the mechanism scale
          rather than measured, because the true count is exactly what differential
          privacy withholds. The flat line is what the runs below actually returned.
        </p>

        {run.isError ? (
          <InlineError message={apiError(run.error, 'The attack run failed.')} />
        ) : null}

        {attempts.length === 0 ? (
          <EmptyState
            icon={Flask}
            title="Not run yet"
            description={`Fires the same query ${RUNS} times against the live gateway and reports every answer and charge.`}
          />
        ) : (
          <>
            <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
              <div className="bg-surface px-4 py-3">
                <span className="label-micro">distinct answers</span>
                <p className="num mt-1 text-h2 text-ok">{distinct.size}</p>
                <p className="mt-0.5 text-micro text-muted">
                  from {attempts.length} queries
                </p>
              </div>
              <div className="bg-surface px-4 py-3">
                <span className="label-micro">total rho charged</span>
                <p className="num mt-1 text-h2 text-text">{fmtRho(totalRho)}</p>
                <p className="mt-0.5 text-micro text-muted">
                  {fmtRho(ATTACK_RHO)} once, then free
                </p>
              </div>
              <div className="bg-surface px-4 py-3">
                <span className="label-micro">served from cache</span>
                <p className="num mt-1 text-h2 text-accent">
                  {pct(attempts.length ? cacheHits / attempts.length : 0)}
                </p>
                <p className="mt-0.5 text-micro text-muted">
                  {cacheHits} of {attempts.length}
                </p>
              </div>
            </div>

            <TableScroll className="rounded-lg border border-border">
              <Table>
                <THead>
                  <tr>
                    <TH className="w-16">run</TH>
                    <TH>status</TH>
                    <TH className="text-right">answer</TH>
                    <TH className="text-right">rho charged</TH>
                  </tr>
                </THead>
                <TBody>
                  {attempts.slice(0, 8).map((attempt) => (
                    <TR key={attempt.index}>
                      <TD className="num text-faint">{attempt.index}</TD>
                      <TD>
                        <StatusChip status={attempt.status} />
                      </TD>
                      <TD className="num text-right font-medium">
                        {metric(attempt.value)}
                      </TD>
                      <TD className="num text-right text-muted">
                        {fmtRho(attempt.rhoCharged)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              {attempts.length > 8 ? (
                <p className="border-t border-border px-4 py-2 text-micro text-faint">
                  {attempts.length - 8} further runs returned the same answer at rho 0.
                </p>
              ) : null}
            </TableScroll>
          </>
        )}
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// Differencing attack
// ---------------------------------------------------------------------------
function DifferencingPanel() {
  const queryClient = useQueryClient()

  const run = useMutation({
    mutationFn: async () => {
      const [a, b] = await Promise.all([
        api.post<DPQueryResponse>('/dp/query/', {
          db: 'safehealth',
          sql: DIFFERENCING_A,
          rho: ATTACK_RHO,
        }),
        api.post<DPQueryResponse>('/dp/query/', {
          db: 'safehealth',
          sql: DIFFERENCING_B,
          rho: ATTACK_RHO,
        }),
      ])
      return { a: a.data, b: b.data }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.budget() })
      void queryClient.invalidateQueries({ queryKey: ['queries'] })
    },
  })

  const a = run.data?.a
  const b = run.data?.b
  const valueA = a ? firstValue(a) : null
  const valueB = b ? firstValue(b) : null
  const delta = valueA !== null && valueB !== null ? valueA - valueB : null
  const rhoSpent = (a?.rho_charged ?? 0) + (b?.rho_charged ?? 0)

  // Variances add across the subtraction, so the difference is noisier than
  // either operand: sd_delta = sd_single * sqrt(2).
  const singleSd = laplaceScaleFromRho(SENSITIVITY, ATTACK_RHO) * Math.SQRT2
  const deltaSd = singleSd * Math.SQRT2

  return (
    <Panel inset>
      <PanelHeader
        title="Differencing attack"
        description="Two overlapping predicates whose difference isolates one group."
        actions={
          <Button
            variant="primary"
            size="sm"
            loading={run.isPending}
            onClick={() => run.mutate()}
          >
            Run pair
            {run.isPending ? null : <Play size={13} weight="fill" aria-hidden />}
          </Button>
        }
      />

      <div className="grid gap-5 p-5">
        <p className="max-w-[70ch] text-small leading-relaxed text-muted">
          Query A counts every charged visit. Query B counts the same set minus the
          emergency admissions. Subtract and the emergency count falls out, without
          ever having been asked for. Narrow the predicates far enough and the
          difference is one person.
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          {[
            { key: 'A', sql: DIFFERENCING_A, response: a, value: valueA },
            { key: 'B', sql: DIFFERENCING_B, response: b, value: valueB },
          ].map((item) => (
            <div key={item.key} className="rounded-lg border border-border p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="label-micro">query {item.key}</span>
                {item.response ? <StatusChip status={item.response.status} /> : null}
              </div>
              <code className="mt-2 block whitespace-pre-wrap text-micro leading-relaxed text-muted">
                {item.sql}
              </code>
              <p className="num mt-3 text-h2 text-text">
                {item.value === null ? '--' : metric(item.value)}
              </p>
              {item.response ? (
                <p className="num mt-1 text-micro text-faint">
                  rho {fmtRho(item.response.rho_charged)} - {item.response.mechanism}
                </p>
              ) : null}
            </div>
          ))}
        </div>

        {run.isError ? (
          <InlineError message={apiError(run.error, 'The attack pair failed.')} />
        ) : null}

        {!run.data ? (
          <EmptyState
            icon={Subtract}
            title="Not run yet"
            description="Runs both predicates against the live gateway and reports the isolated difference with its noise."
          />
        ) : (
          <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
            <div className="bg-surface px-4 py-3">
              <span className="label-micro">isolated difference</span>
              <p className="num mt-1 text-h2 text-warn">
                {delta === null ? '--' : metric(delta)}
              </p>
              <p className="mt-0.5 text-micro text-muted">A minus B</p>
            </div>
            <div className="bg-surface px-4 py-3">
              <Tooltip content="Noise variances add across a subtraction, so the difference is 1.41x noisier than either answer on its own.">
                <span className="label-micro cursor-help">noise on the difference</span>
              </Tooltip>
              <p className="num mt-1 text-h2 text-text">+/- {metric(deltaSd)}</p>
              <p className="mt-0.5 text-micro text-muted">
                sd, vs {metric(singleSd)} per query
              </p>
            </div>
            <div className="bg-surface px-4 py-3">
              <span className="label-micro">rho spent</span>
              <p className="num mt-1 text-h2 text-danger">{fmtRho(rhoSpent)}</p>
              <p className="mt-0.5 text-micro text-muted">both queries charged in full</p>
            </div>
          </div>
        )}

        <div className="flex items-start gap-2 rounded-sm border border-border bg-surface-2 px-3 py-2.5">
          <Info size={14} className="mt-0.5 shrink-0 text-faint" aria-hidden />
          <p className="text-micro leading-relaxed text-muted">
            The defence here is composition, not detection: zCDP adds, so the pair
            costs twice, and the account runs out before the attacker can narrow the
            predicates enough to isolate a person. Predicate-overlap scoring
            (allow / surcharge / block) is still a backend task. The
            <span className="num"> sys_predicate_overlap </span>
            table exists but has no API yet, so this panel reports what the gateway
            actually did rather than a detection verdict it did not make.
          </p>
        </div>
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------
export default function AttackLabPage() {
  const budget = useBudget()
  const account = budget.data

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Attack lab"
        description="Two classic attacks on a noisy query interface, run against the live gateway. Both spend real budget from your account."
        actions={
          account ? (
            <div className="w-48">
              <div className="flex items-baseline justify-between">
                <span className="label-micro">your budget</span>
                <span className="num text-micro text-text">
                  {fmtRho(account.rho_remaining)} / {fmtRho(account.rho_total, 2)}
                </span>
              </div>
              <RhoMeter
                remaining={account.rho_remaining}
                total={account.rho_total}
                className="mt-1.5"
              />
            </div>
          ) : null
        }
      />

      {account && account.rho_remaining < ATTACK_RHO * 3 ? (
        <InlineError
          message={
            <>
              Low budget. These runs need roughly
              <span className="num"> {fmtRho(ATTACK_RHO * 3, 2)} </span>
              rho and will start coming back as
              <Badge tone="danger" className="mx-1">
                rejected: budget
              </Badge>
              once the account is exhausted, which is itself the defence working.
            </>
          }
        />
      ) : null}

      <div className="grid gap-6">
        <AveragingPanel />
        <DifferencingPanel />
      </div>
    </div>
  )
}
