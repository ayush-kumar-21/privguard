import { Plus } from '@phosphor-icons/react'
import { useState } from 'react'

import { DataTable, type Column } from '../../components/data-table/DataTable'
import { ConfirmDelete, ResourceForm } from '../../components/data-table/ResourceForm'
import { RowActions } from '../../components/data-table/RowActions'
import { useServerTable } from '../../components/data-table/useServerTable'
import { Badge, Dot } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { PageHeader } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import { int } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import { useDebounced } from '../../lib/useDebounced'
import type { Diagnosis } from '../../lib/types'
import { useAuth } from '../../stores/auth'

const ENDPOINT = '/diagnoses/'

const PRIMARY_OPTIONS = [
  { value: '', label: 'Primary and secondary' },
  { value: 'true', label: 'Primary only' },
  { value: 'false', label: 'Secondary only' },
]

/** Z99.9 is the seeded rare code, the signal the attack lab goes after. */
const RARE_CODE = 'Z99.9'

export default function DiagnosesPage() {
  const canWrite = useAuth((state) => state.hasRole('clinician'))
  const [searchInput, setSearchInput] = useState('')
  const [isPrimary, setIsPrimary] = useState('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Diagnosis | null>(null)
  const [deleting, setDeleting] = useState<Diagnosis | null>(null)

  const table = useServerTable<Diagnosis>({
    endpoint: ENDPOINT,
    keyFactory: qk.diagnoses,
    initialSort: { field: 'diagnosis_id', desc: false },
    search: useDebounced(searchInput),
    filters: { is_primary: isPrimary },
  })

  const columns: Column<Diagnosis>[] = [
    {
      key: 'diagnosis_id',
      header: 'id',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num text-muted">{row.diagnosis_id}</span>,
    },
    {
      key: 'visit',
      header: 'visit',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num">{row.visit}</span>,
    },
    {
      key: 'icd10_code',
      header: 'icd-10',
      skeletonWidth: 'w-16',
      render: (row) => (
        <Badge tone={row.icd10_code === RARE_CODE ? 'warn' : 'outline'}>
          <span className="num">{row.icd10_code}</span>
        </Badge>
      ),
    },
    {
      key: 'is_primary',
      header: 'rank',
      skeletonWidth: 'w-16',
      render: (row) =>
        row.is_primary ? (
          <Badge tone="accent">
            <Dot />
            primary
          </Badge>
        ) : (
          <span className="text-faint">secondary</span>
        ),
    },
    {
      key: 'severity',
      header: 'severity',
      align: 'right',
      skeletonWidth: 'w-8',
      render: (row) => <span className="num">{int(row.severity)}</span>,
    },
  ]

  const formFields = [
    { name: 'visit', label: 'Visit id', type: 'number' as const, required: true },
    {
      name: 'icd10_code',
      label: 'ICD-10 code',
      type: 'text' as const,
      required: true,
      description: 'Rare codes are exactly the values DP partition selection suppresses.',
    },
    { name: 'severity', label: 'Severity (1-5)', type: 'number' as const },
    { name: 'is_primary', label: 'Primary diagnosis', type: 'boolean' as const },
  ]

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Diagnoses"
        description="Coded conditions per visit. A code carried by a single patient is a re-identification risk, which is why grouped queries drop small partitions."
        actions={
          canWrite ? (
            <Button variant="primary" size="md" onClick={() => setCreating(true)}>
              <Plus size={14} weight="bold" aria-hidden />
              New diagnosis
            </Button>
          ) : null
        }
      />

      <DataTable
        columns={columns}
        rows={table.rows}
        rowKey={(row) => row.diagnosis_id}
        count={table.count}
        page={table.page}
        pageCount={table.pageCount}
        onPageChange={table.setPage}
        sort={table.sort}
        onToggleSort={table.toggleSort}
        isInitialLoading={table.isInitialLoading}
        isFetching={table.isFetching}
        error={table.query.error}
        onRetry={() => void table.query.refetch()}
        search={searchInput}
        onSearchChange={setSearchInput}
        searchPlaceholder="Search ICD-10 code"
        emptyTitle="No diagnoses match"
        emptyDescription="Try a different code or clear the rank filter."
        toolbar={
          <Select
            value={isPrimary}
            onValueChange={setIsPrimary}
            options={PRIMARY_OPTIONS}
            size="sm"
            className="w-[13rem]"
            aria-label="Filter by rank"
          />
        }
        rowActions={
          canWrite
            ? (row) => (
                <RowActions
                  label={`diagnosis ${row.diagnosis_id}`}
                  onEdit={() => setEditing(row)}
                  onDelete={() => setDeleting(row)}
                />
              )
            : undefined
        }
      />

      {creating || editing ? (
        <ResourceForm
          open
          onOpenChange={(open) => {
            if (!open) {
              setCreating(false)
              setEditing(null)
            }
          }}
          title={editing ? `Edit diagnosis ${editing.diagnosis_id}` : 'New diagnosis'}
          endpoint={ENDPOINT}
          idKey="diagnosis_id"
          invalidateKey={qk.diagnoses()}
          row={editing ? (editing as unknown as Record<string, unknown>) : undefined}
          fields={formFields}
        />
      ) : null}

      {deleting ? (
        <ConfirmDelete
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          endpoint={ENDPOINT}
          id={deleting.diagnosis_id}
          label={`diagnosis ${deleting.diagnosis_id}`}
          invalidateKey={qk.diagnoses()}
        />
      ) : null}
    </div>
  )
}
