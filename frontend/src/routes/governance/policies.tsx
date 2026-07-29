import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FloppyDisk, Sliders } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { EmptyState, ErrorState, InlineError, Skeleton } from '../../components/states'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Tooltip } from '../../components/ui/misc'
import { PageHeader, Panel, PanelHeader } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import api, { apiError, fetchAll } from '../../lib/api'
import { qk } from '../../lib/queryKeys'
import type { ColumnPrivacy, Mechanism, TablePrivacy } from '../../lib/types'

const MECHANISMS: { value: Mechanism; label: string }[] = [
  { value: 'laplace', label: 'laplace' },
  { value: 'gaussian', label: 'gaussian' },
  { value: 'discrete_laplace', label: 'discrete laplace' },
  { value: 'none', label: 'none' },
]

/** Small labelled control used across both policy editors. */
function Cell({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  const control = <div className="mt-1">{children}</div>
  return (
    <div className="min-w-0">
      {hint ? (
        <Tooltip content={hint}>
          <span className="label-micro cursor-help border-b border-dotted border-border-strong">
            {label}
          </span>
        </Tooltip>
      ) : (
        <span className="label-micro">{label}</span>
      )}
      {control}
    </div>
  )
}

// --- Column privacy ---------------------------------------------------------
function ColumnPolicyRow({ policy }: { policy: ColumnPrivacy }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState(policy)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(policy), [policy])

  const dirty =
    draft.mechanism !== policy.mechanism ||
    draft.lower_clamp !== policy.lower_clamp ||
    draft.upper_clamp !== policy.upper_clamp ||
    draft.min_partition_size !== policy.min_partition_size ||
    draft.is_groupable !== policy.is_groupable

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/column-policies/${policy.policy_id}/`, {
        mechanism: draft.mechanism,
        lower_clamp: draft.lower_clamp,
        upper_clamp: draft.upper_clamp,
        min_partition_size: draft.min_partition_size,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.columnPolicies() })
      toast.success(`Column #${policy.column} policy saved`)
      setError(null)
    },
    onError: (err) => setError(apiError(err, 'The policy could not be saved.')),
  })

  return (
    <div className="grid gap-3 border-b border-border px-5 py-4 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="num text-small text-text">column #{policy.column}</span>
        <span className="text-micro text-faint">policy #{policy.policy_id}</span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Cell
          label="mechanism"
          hint="How noise is drawn. Gaussian composes cleanly in rho for multi-output aggregates; discrete Laplace keeps counts integral."
        >
          <Select
            value={draft.mechanism}
            onValueChange={(value) =>
              setDraft({ ...draft, mechanism: value as Mechanism })
            }
            options={MECHANISMS}
            size="sm"
            aria-label={`Mechanism for column ${policy.column}`}
          />
        </Cell>

        <Cell
          label="lower clamp"
          hint="Values below this are pulled up before aggregation. Clamp bounds are what make sensitivity finite."
        >
          <Input
            type="number"
            value={draft.lower_clamp ?? ''}
            onChange={(event) =>
              setDraft({
                ...draft,
                lower_clamp: event.target.value === '' ? null : Number(event.target.value),
              })
            }
            className="num h-8 text-micro"
            aria-label={`Lower clamp for column ${policy.column}`}
          />
        </Cell>

        <Cell label="upper clamp" hint="Values above this are pulled down.">
          <Input
            type="number"
            value={draft.upper_clamp ?? ''}
            onChange={(event) =>
              setDraft({
                ...draft,
                upper_clamp: event.target.value === '' ? null : Number(event.target.value),
              })
            }
            className="num h-8 text-micro"
            aria-label={`Upper clamp for column ${policy.column}`}
          />
        </Cell>

        <Cell
          label="min partition"
          hint="Groups with fewer contributing patients than this are candidates for suppression."
        >
          <Input
            type="number"
            value={draft.min_partition_size}
            onChange={(event) =>
              setDraft({ ...draft, min_partition_size: Number(event.target.value) })
            }
            className="num h-8 text-micro"
            aria-label={`Minimum partition size for column ${policy.column}`}
          />
        </Cell>

        <div className="flex items-end">
          <Button
            size="sm"
            variant={dirty ? 'primary' : 'secondary'}
            disabled={!dirty}
            loading={save.isPending}
            onClick={() => save.mutate()}
            className="w-full"
          >
            <FloppyDisk size={13} aria-hidden />
            Save
          </Button>
        </div>
      </div>

      {error ? <InlineError message={error} /> : null}
    </div>
  )
}

// --- Table privacy ----------------------------------------------------------
function TablePolicyRow({ policy }: { policy: TablePrivacy }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState(policy)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(policy), [policy])

  const dirty =
    draft.max_rows_per_unit !== policy.max_rows_per_unit ||
    draft.max_groups_per_unit !== policy.max_groups_per_unit ||
    draft.min_partition_rho !== policy.min_partition_rho

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/table-policies/${policy.table}/`, {
        max_rows_per_unit: draft.max_rows_per_unit,
        max_groups_per_unit: draft.max_groups_per_unit,
        min_partition_rho: draft.min_partition_rho,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.tablePolicies() })
      toast.success(`Table #${policy.table} policy saved`)
      setError(null)
    },
    onError: (err) => setError(apiError(err, 'The policy could not be saved.')),
  })

  return (
    <div className="grid gap-3 border-b border-border px-5 py-4 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="num text-small text-text">table #{policy.table}</span>
        <span className="num text-micro text-faint">
          privacy unit = column #{policy.privacy_unit_col}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Cell
          label="max rows per unit"
          hint="The L-infinity contribution bound C. One patient contributes at most this many rows to any aggregate."
        >
          <Input
            type="number"
            value={draft.max_rows_per_unit}
            onChange={(event) =>
              setDraft({ ...draft, max_rows_per_unit: Number(event.target.value) })
            }
            className="num h-8 text-micro"
            aria-label="Max rows per unit"
          />
        </Cell>

        <Cell
          label="max groups per unit"
          hint="The L0 bound G. One patient can influence at most this many GROUP BY partitions."
        >
          <Input
            type="number"
            value={draft.max_groups_per_unit}
            onChange={(event) =>
              setDraft({ ...draft, max_groups_per_unit: Number(event.target.value) })
            }
            className="num h-8 text-micro"
            aria-label="Max groups per unit"
          />
        </Cell>

        <Cell
          label="min partition rho"
          hint="Budget reserved for DP partition selection before any group is released."
        >
          <Input
            type="number"
            step="0.001"
            value={draft.min_partition_rho}
            onChange={(event) =>
              setDraft({ ...draft, min_partition_rho: Number(event.target.value) })
            }
            className="num h-8 text-micro"
            aria-label="Minimum partition rho"
          />
        </Cell>

        <div className="flex items-end">
          <Button
            size="sm"
            variant={dirty ? 'primary' : 'secondary'}
            disabled={!dirty}
            loading={save.isPending}
            onClick={() => save.mutate()}
            className="w-full"
          >
            <FloppyDisk size={13} aria-hidden />
            Save
          </Button>
        </div>
      </div>

      {error ? <InlineError message={error} /> : null}
    </div>
  )
}

export default function PoliciesPage() {
  const columnPolicies = useQuery({
    queryKey: qk.columnPolicies(),
    queryFn: () => fetchAll<ColumnPrivacy>('/column-policies/'),
  })

  const tablePolicies = useQuery({
    queryKey: qk.tablePolicies(),
    queryFn: () => fetchAll<TablePrivacy>('/table-policies/'),
  })

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Privacy policies"
        description="The knobs that decide how much noise a query gets and which groups survive. Changing them changes every future answer, so each row saves on its own."
      />

      <Panel inset>
        <PanelHeader
          title="Column privacy"
          description="mechanism, clamp bounds and partition floor, per column"
        />
        {columnPolicies.isError ? (
          <ErrorState
            message={apiError(columnPolicies.error, 'Column policies could not be loaded.')}
            onRetry={() => void columnPolicies.refetch()}
          />
        ) : columnPolicies.isPending ? (
          <div className="grid gap-3 p-5">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : columnPolicies.data.length === 0 ? (
          <EmptyState
            icon={Sliders}
            title="No column policies"
            description="Without a policy and clamp bounds, SUM and AVG on a column are refused outright."
          />
        ) : (
          columnPolicies.data.map((policy) => (
            <ColumnPolicyRow key={policy.policy_id} policy={policy} />
          ))
        )}
      </Panel>

      <Panel inset>
        <PanelHeader
          title="Table privacy"
          description="privacy unit and contribution bounds, per table"
        />
        {tablePolicies.isError ? (
          <ErrorState
            message={apiError(tablePolicies.error, 'Table policies could not be loaded.')}
            onRetry={() => void tablePolicies.refetch()}
          />
        ) : tablePolicies.isPending ? (
          <div className="grid gap-3 p-5">
            <Skeleton className="h-20 w-full" />
          </div>
        ) : tablePolicies.data.length === 0 ? (
          <EmptyState
            icon={Sliders}
            title="No table policies"
            description="Run manage.py seed_demo to declare the visits privacy unit."
          />
        ) : (
          tablePolicies.data.map((policy) => (
            <TablePolicyRow key={policy.table} policy={policy} />
          ))
        )}
      </Panel>
    </div>
  )
}
