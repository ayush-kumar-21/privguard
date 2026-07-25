import { Plus } from '@phosphor-icons/react'
import { useState } from 'react'

import { DataTable, type Column } from '../../components/data-table/DataTable'
import { ConfirmDelete, ResourceForm } from '../../components/data-table/ResourceForm'
import { RowActions } from '../../components/data-table/RowActions'
import { useServerTable } from '../../components/data-table/useServerTable'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { PageHeader } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import { optionLabel, useOptions } from '../../hooks/useOptions'
import { currency, dateTime, int } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import type { Visit } from '../../lib/types'
import { useAuth } from '../../stores/auth'

const ENDPOINT = '/visits/'

const ADMISSION_OPTIONS = [
  { value: '', label: 'Any admission' },
  { value: 'EMERGENCY', label: 'EMERGENCY' },
  { value: 'ELECTIVE', label: 'ELECTIVE' },
  { value: 'URGENT', label: 'URGENT' },
]

const ADMISSION_TONE: Record<string, 'danger' | 'accent' | 'warn'> = {
  EMERGENCY: 'danger',
  ELECTIVE: 'accent',
  URGENT: 'warn',
}

export default function VisitsPage() {
  const canWrite = useAuth((state) => state.hasRole('clinician'))
  const [admissionType, setAdmissionType] = useState('')
  const [dept, setDept] = useState('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Visit | null>(null)
  const [deleting, setDeleting] = useState<Visit | null>(null)

  const departments = useOptions({
    endpoint: '/departments/',
    valueKey: 'dept_id',
    labelKey: 'dept_name',
    queryKey: qk.departments(),
  })

  const table = useServerTable<Visit>({
    endpoint: ENDPOINT,
    keyFactory: qk.visits,
    initialSort: { field: 'admit_ts', desc: true },
    filters: { admission_type: admissionType, dept },
  })

  const deptOptions = [{ value: '', label: 'Any department' }, ...(departments.data ?? [])]

  const columns: Column<Visit>[] = [
    {
      key: 'visit_id',
      header: 'id',
      sortable: true,
      skeletonWidth: 'w-12',
      render: (row) => <span className="num text-muted">{row.visit_id}</span>,
    },
    {
      key: 'patient',
      header: 'patient',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num">{row.patient}</span>,
    },
    {
      key: 'dept',
      header: 'department',
      skeletonWidth: 'w-20',
      render: (row) => optionLabel(departments.data, row.dept),
    },
    {
      key: 'admission_type',
      header: 'admission',
      skeletonWidth: 'w-20',
      render: (row) => (
        <Badge tone={ADMISSION_TONE[row.admission_type] ?? 'neutral'}>
          {row.admission_type}
        </Badge>
      ),
    },
    {
      key: 'admit_ts',
      header: 'admitted',
      sortable: true,
      skeletonWidth: 'w-24',
      render: (row) => <span className="num text-muted">{dateTime(row.admit_ts)}</span>,
    },
    {
      key: 'length_of_stay_days',
      header: 'stay',
      align: 'right',
      skeletonWidth: 'w-10',
      render: (row) => (
        <span className="num text-muted">
          {row.length_of_stay_days === null ? '--' : `${int(row.length_of_stay_days)}d`}
        </span>
      ),
    },
    {
      key: 'total_charge',
      header: 'charge',
      sortable: true,
      align: 'right',
      skeletonWidth: 'w-16',
      render: (row) => <span className="num">{currency(row.total_charge)}</span>,
    },
  ]

  const formFields = [
    {
      name: 'patient',
      label: 'Patient id',
      type: 'number' as const,
      required: true,
      description: 'The privacy unit this visit belongs to.',
    },
    {
      name: 'dept',
      label: 'Department',
      type: 'select' as const,
      required: true,
      options: departments.data ?? [],
    },
    { name: 'attending_staff', label: 'Attending staff id', type: 'number' as const, required: true },
    { name: 'admit_ts', label: 'Admitted', type: 'datetime' as const, required: true },
    { name: 'discharge_ts', label: 'Discharged', type: 'datetime' as const },
    {
      name: 'total_charge',
      label: 'Total charge',
      type: 'number' as const,
      required: true,
      step: '0.01',
      description: 'Clamped to 0 - 50,000 before any DP aggregate.',
    },
    {
      name: 'admission_type',
      label: 'Admission type',
      type: 'select' as const,
      required: true,
      options: ADMISSION_OPTIONS.filter((option) => option.value !== ''),
    },
  ]

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Visits"
        description="The fact table every DP aggregate runs over. Contribution bounds cap each patient at 5 rows and 3 groups."
        actions={
          canWrite ? (
            <Button variant="primary" size="md" onClick={() => setCreating(true)}>
              <Plus size={14} weight="bold" aria-hidden />
              New visit
            </Button>
          ) : null
        }
      />

      <DataTable
        columns={columns}
        rows={table.rows}
        rowKey={(row) => row.visit_id}
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
        emptyTitle="No visits match"
        emptyDescription="Clear the filters to see the full fact table."
        toolbar={
          <>
            <Select
              value={admissionType}
              onValueChange={setAdmissionType}
              options={ADMISSION_OPTIONS}
              size="sm"
              className="w-[11rem]"
              aria-label="Filter by admission type"
            />
            <Select
              value={dept}
              onValueChange={setDept}
              options={deptOptions}
              size="sm"
              className="w-[11rem]"
              aria-label="Filter by department"
            />
          </>
        }
        rowActions={
          canWrite
            ? (row) => (
                <RowActions
                  label={`visit ${row.visit_id}`}
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
          title={editing ? `Edit visit ${editing.visit_id}` : 'New visit'}
          endpoint={ENDPOINT}
          idKey="visit_id"
          invalidateKey={qk.visits()}
          row={editing ? (editing as unknown as Record<string, unknown>) : undefined}
          fields={formFields}
        />
      ) : null}

      {deleting ? (
        <ConfirmDelete
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          endpoint={ENDPOINT}
          id={deleting.visit_id}
          label={`visit ${deleting.visit_id}`}
          invalidateKey={qk.visits()}
        />
      ) : null}
    </div>
  )
}
