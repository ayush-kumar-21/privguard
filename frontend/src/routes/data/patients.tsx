import { Info } from '@phosphor-icons/react'
import { useState } from 'react'

import { DataTable, type Column } from '../../components/data-table/DataTable'
import { ConfirmDelete, ResourceForm } from '../../components/data-table/ResourceForm'
import { RowActions } from '../../components/data-table/RowActions'
import { useServerTable } from '../../components/data-table/useServerTable'
import { Badge } from '../../components/ui/badge'
import { Tooltip } from '../../components/ui/misc'
import { PageHeader } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import { optionLabel, useOptions } from '../../hooks/useOptions'
import { date, int } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import { useDebounced } from '../../lib/useDebounced'
import type { Patient } from '../../lib/types'
import { useAuth } from '../../stores/auth'

const ENDPOINT = '/patients/'

const SEX_OPTIONS = [
  { value: '', label: 'Any sex' },
  { value: 'F', label: 'F' },
  { value: 'M', label: 'M' },
]

const INSURANCE_OPTIONS = [
  { value: '', label: 'Any insurance' },
  { value: 'PPO', label: 'PPO' },
  { value: 'HMO', label: 'HMO' },
  { value: 'MEDICARE', label: 'MEDICARE' },
  { value: 'SELF', label: 'SELF' },
]

export default function PatientsPage() {
  const canWrite = useAuth((state) => state.hasRole('clinician'))
  const [searchInput, setSearchInput] = useState('')
  const [sex, setSex] = useState('')
  const [insurance, setInsurance] = useState('')
  const [editing, setEditing] = useState<Patient | null>(null)
  const [deleting, setDeleting] = useState<Patient | null>(null)

  const regions = useOptions({
    endpoint: '/regions/',
    valueKey: 'region_id',
    labelKey: 'region_name',
    queryKey: qk.regions(),
  })

  const table = useServerTable<Patient>({
    endpoint: ENDPOINT,
    keyFactory: qk.patients,
    initialSort: { field: 'patient_id', desc: false },
    search: useDebounced(searchInput),
    filters: { sex, insurance_type: insurance },
  })

  const columns: Column<Patient>[] = [
    {
      key: 'patient_id',
      header: 'id',
      sortable: true,
      skeletonWidth: 'w-10',
      render: (row) => <span className="num text-muted">{row.patient_id}</span>,
    },
    {
      key: 'region',
      header: 'region',
      skeletonWidth: 'w-16',
      render: (row) => optionLabel(regions.data, row.region),
    },
    {
      key: 'sex',
      header: 'sex',
      skeletonWidth: 'w-6',
      render: (row) => <span className="num">{row.sex}</span>,
    },
    {
      key: 'birth_year',
      header: 'age',
      sortable: true,
      align: 'right',
      skeletonWidth: 'w-8',
      render: (row) => <span className="num">{int(row.age)}</span>,
    },
    {
      key: 'insurance_type',
      header: 'insurance',
      skeletonWidth: 'w-20',
      render: (row) => <Badge tone="outline">{row.insurance_type}</Badge>,
    },
    {
      key: 'zip3',
      header: 'zip3',
      skeletonWidth: 'w-10',
      render: (row) => <span className="num text-muted">{row.zip3}</span>,
    },
    {
      key: 'enrolled_at',
      header: 'enrolled',
      align: 'right',
      skeletonWidth: 'w-20',
      render: (row) => <span className="num text-muted">{date(row.enrolled_at)}</span>,
    },
  ]

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Patients"
        description="The privacy unit. Neighbouring databases differ by exactly one row here, which is what every rho charge is protecting."
        actions={
          <Tooltip content="mrn is access-mode denied, so the API never accepts a client-supplied patient record. Creation is a backend task.">
            <span className="inline-flex items-center gap-1.5 text-micro text-faint">
              <Info size={13} aria-hidden />
              create is server-side only
            </span>
          </Tooltip>
        }
      />

      <DataTable
        columns={columns}
        rows={table.rows}
        rowKey={(row) => row.patient_id}
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
        searchPlaceholder="Search zip3 or insurance"
        emptyTitle="No patients match"
        emptyDescription="Clear the filters or widen the search."
        toolbar={
          <>
            <Select
              value={sex}
              onValueChange={setSex}
              options={SEX_OPTIONS}
              size="sm"
              className="w-[7.5rem]"
              aria-label="Filter by sex"
            />
            <Select
              value={insurance}
              onValueChange={setInsurance}
              options={INSURANCE_OPTIONS}
              size="sm"
              className="w-[10rem]"
              aria-label="Filter by insurance"
            />
          </>
        }
        rowActions={
          canWrite
            ? (row) => (
                <RowActions
                  label={`patient ${row.patient_id}`}
                  onEdit={() => setEditing(row)}
                  onDelete={() => setDeleting(row)}
                />
              )
            : undefined
        }
      />

      {editing ? (
        <ResourceForm
          open
          onOpenChange={(open) => !open && setEditing(null)}
          title={`Edit patient ${editing.patient_id}`}
          description="mrn is not editable here: it is an access-controlled column."
          endpoint={ENDPOINT}
          idKey="patient_id"
          invalidateKey={qk.patients()}
          row={editing as unknown as Record<string, unknown>}
          fields={[
            {
              name: 'region',
              label: 'Region',
              type: 'select',
              required: true,
              options: regions.data ?? [],
            },
            { name: 'birth_year', label: 'Birth year', type: 'number', required: true },
            {
              name: 'sex',
              label: 'Sex',
              type: 'select',
              required: true,
              options: SEX_OPTIONS.filter((option) => option.value !== ''),
            },
            { name: 'zip3', label: 'ZIP3', type: 'text', required: true },
            {
              name: 'insurance_type',
              label: 'Insurance',
              type: 'select',
              required: true,
              options: INSURANCE_OPTIONS.filter((option) => option.value !== ''),
            },
            { name: 'enrolled_at', label: 'Enrolled', type: 'date', required: true },
          ]}
        />
      ) : null}

      {deleting ? (
        <ConfirmDelete
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          endpoint={ENDPOINT}
          id={deleting.patient_id}
          label={`patient ${deleting.patient_id}`}
          invalidateKey={qk.patients()}
        />
      ) : null}
    </div>
  )
}
