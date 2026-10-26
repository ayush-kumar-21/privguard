import { useMutation, useQuery } from '@tanstack/react-query'
import { CaretLeft, CaretRight, HeartStraight, ShieldCheck, ShieldWarning } from '@phosphor-icons/react'
import { useState } from 'react'

import { RhoMeter } from '../../components/privacy/RhoMeter'
import { StatRow, StatTile } from '../../components/privacy/StatTile'
import { EmptyState, ErrorState, SkeletonRows } from '../../components/states'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Select } from '../../components/ui/select'
import { PageHeader, Panel, PanelHeader } from '../../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../../components/ui/table'
import api, { apiError } from '../../lib/api'
import { int, rho } from '../../lib/format'
import type { Paginated } from '../../lib/types'

interface PatientBudget {
  patient_id: number
  rho_total: number
  rho_spent: number
  rho_remaining: number
  status: string
  charges: number
  last_charged_at: string | null
}

interface Summary {
  patients: number
  active: number
  exhausted: number
  erased: number
  charges: number
  rho_spent: number
  rho_total: number
  mean_remaining: number
}

interface Verdict {
  ok: boolean
  entries: number
  first_bad_entry: number | null
}

/** Replays one patient's hash chain on the server and shows the verdict in place. */
function VerifyCell({ patientId }: { patientId: number }) {
  const check = useMutation({
    mutationFn: async () => (await api.get<Verdict>(`/patient-budgets/${patientId}/verify/`)).data,
  })
  if (check.data) {
    return check.data.ok ? (
      <span className="inline-flex items-center gap-1.5 text-small text-ok">
        <ShieldCheck size={14} weight="fill" aria-hidden />
        intact ({check.data.entries})
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 text-small text-danger">
        <ShieldWarning size={14} weight="fill" aria-hidden />
        broken at {check.data.first_bad_entry}
      </span>
    )
  }
  return (
    <Button variant="ghost" size="sm" loading={check.isPending} onClick={() => check.mutate()}>
      Verify chain
    </Button>
  )
}

export default function PatientBudgetsPage() {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')

  const summary = useQuery({
    queryKey: ['patient-budgets', 'summary'],
    queryFn: async () => (await api.get<Summary>('/patient-budgets/summary/')).data,
    refetchInterval: 4_000, // moves as analysts query
  })
  const list = useQuery({
    queryKey: ['patient-budgets', 'list', page, status],
    queryFn: async () =>
      (await api.get<Paginated<PatientBudget>>('/patient-budgets/', { params: { page, status: status || undefined } }))
        .data,
    refetchInterval: 4_000,
    placeholderData: (previous) => previous,
  })

  const s = summary.data
  return (
    <div className="grid gap-6">
      <PageHeader
        title="Patient budgets"
        description="Every patient has their own privacy allowance, shared by all analysts. A query charges only the patients it can affect, and a patient whose allowance is spent is left out of later queries. Opening another analyst account never buys more. Administrators only: this shows exactly which patients each query touched, so administrators cannot run queries."
      />

      <StatRow>
        <StatTile label="patients" loading={summary.isPending} value={s ? int(s.patients) : '--'}
          detail={s ? `${int(s.active)} active - ${int(s.erased)} erased` : null} />
        <StatTile label="exhausted" loading={summary.isPending} tone={s && s.exhausted > 0 ? 'warn' : 'default'}
          value={s ? int(s.exhausted) : '--'} detail="Left out of every further query" />
        <StatTile label="charges" loading={summary.isPending} value={s ? int(s.charges) : '--'}
          detail="Patient ledger entries" />
        <StatTile label="mean remaining" loading={summary.isPending} value={s ? rho(s.mean_remaining, 2) : '--'}
          detail={s ? `${rho(s.rho_spent, 2)} spent of ${rho(s.rho_total, 0)}` : null} />
      </StatRow>

      <Panel inset>
        <PanelHeader
          title="Most charged patients first"
          description="from v_patient_budget. Patient ids only: no names or record numbers."
          actions={
            <Select
              size="sm"
              value={status}
              onValueChange={(v) => { setStatus(v); setPage(1) }}
              aria-label="Filter by status"
              placeholder="All statuses"
              options={[
                { value: 'active', label: 'active' },
                { value: 'exhausted', label: 'exhausted' },
                { value: 'erased', label: 'erased' },
              ]}
            />
          }
        />
        {list.isError ? (
          <ErrorState message={apiError(list.error, 'Patient budgets could not be loaded.')}
            onRetry={() => void list.refetch()} />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH>patient</TH>
                  <TH className="min-w-[10rem]">remaining</TH>
                  <TH className="text-right">spent</TH>
                  <TH className="text-right">allowance</TH>
                  <TH className="text-right">charges</TH>
                  <TH>status</TH>
                  <TH>chain</TH>
                </tr>
              </THead>
              <TBody>
                {list.isPending ? (
                  <SkeletonRows rows={6} widths={['w-14', 'w-28', 'w-12', 'w-12', 'w-10', 'w-16', 'w-20']} />
                ) : list.data.results.length === 0 ? (
                  <tr>
                    <td colSpan={7}>
                      <EmptyState icon={HeartStraight} title="No patients match" description="Try another status." />
                    </td>
                  </tr>
                ) : (
                  list.data.results.map((p) => (
                    <TR key={p.patient_id}>
                      <TD className="num">#{p.patient_id}</TD>
                      <TD>
                        <div className="flex items-center gap-2.5">
                          <span className="num w-12 shrink-0">{rho(p.rho_remaining)}</span>
                          <RhoMeter remaining={p.rho_remaining} total={p.rho_total} className="w-24" height="h-1" animate={false} />
                        </div>
                      </TD>
                      <TD className="num text-right">{rho(p.rho_spent)}</TD>
                      <TD className="num text-right text-muted">{rho(p.rho_total, 2)}</TD>
                      <TD className="num text-right text-muted">{int(p.charges)}</TD>
                      <TD>
                        <Badge tone={p.status === 'active' ? 'ok' : p.status === 'exhausted' ? 'danger' : 'neutral'}>
                          {p.status}
                        </Badge>
                      </TD>
                      <TD><VerifyCell patientId={p.patient_id} /></TD>
                    </TR>
                  ))
                )}
              </TBody>
            </Table>
          </TableScroll>
        )}
        {list.data ? (
          <div className="flex items-center justify-between border-t border-border px-4 py-2.5 text-small text-muted">
            <span className="num">{int(list.data.count)} patients</span>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" disabled={!list.data.previous} onClick={() => setPage((n) => n - 1)}>
                <CaretLeft size={13} aria-hidden /> Prev
              </Button>
              <span className="num">page {page}</span>
              <Button variant="ghost" size="sm" disabled={!list.data.next} onClick={() => setPage((n) => n + 1)}>
                Next <CaretRight size={13} aria-hidden />
              </Button>
            </div>
          </div>
        ) : null}
      </Panel>
    </div>
  )
}
