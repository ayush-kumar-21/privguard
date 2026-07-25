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
import { dateTime, metric } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import { useDebounced } from '../../lib/useDebounced'
import type { LabResult } from '../../lib/types'
import { useAuth } from '../../stores/auth'

const ENDPOINT = '/lab-results/'

const TEST_OPTIONS = [
  { value: '', label: 'All tests' },
  { value: 'GLU', label: 'GLU  glucose' },
  { value: 'HGB', label: 'HGB  haemoglobin' },
  { value: 'CRP', label: 'CRP  c-reactive protein' },
]

export default function LabsPage() {
  const canWrite = useAuth((state) => state.hasRole('clinician'))
  const [searchInput, setSearchInput] = useState('')
  const [testCode, setTestCode] = useState('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<LabResult | null>(null)
  const [deleting, setDeleting] = useState<LabResult | null>(null)

  const table = useServerTable<LabResult>({
    endpoint: ENDPOINT,
    keyFactory: qk.labResults,
    initialSort: { field: 'lab_id', desc: false },
    search: useDebounced(searchInput),
    filters: { test_code: testCode },
  })

  const columns: Column<LabResult>[] = [
    {
      key: 'lab_id',
      header: 'id',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num text-muted">{row.lab_id}</span>,
    },
    {
      key: 'visit',
      header: 'visit',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num">{row.visit}</span>,
    },
    {
      key: 'test_code',
      header: 'test',
      skeletonWidth: 'w-12',
      render: (row) => (
        <Badge tone="outline">
          <span className="num">{row.test_code}</span>
        </Badge>
      ),
    },
    {
      key: 'value_num',
      header: 'value',
      align: 'right',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num">{metric(row.value_num)}</span>,
    },
    {
      key: 'unit',
      header: 'unit',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num text-faint">{row.unit ?? '--'}</span>,
    },
    {
      key: 'taken_at',
      header: 'taken',
      align: 'right',
      skeletonWidth: 'w-24',
      render: (row) => <span className="num text-muted">{dateTime(row.taken_at)}</span>,
    },
  ]

  const formFields = [
    { name: 'visit', label: 'Visit id', type: 'number' as const, required: true },
    {
      name: 'test_code',
      label: 'Test code',
      type: 'select' as const,
      required: true,
      options: TEST_OPTIONS.filter((option) => option.value !== ''),
    },
    { name: 'value_num', label: 'Value', type: 'number' as const, step: '0.1' },
    { name: 'unit', label: 'Unit', type: 'text' as const },
    { name: 'taken_at', label: 'Taken at', type: 'datetime' as const, required: true },
  ]

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Lab results"
        description="Measurements per visit. No column privacy policy is declared for these values yet, so they are not available to DP aggregates."
        actions={
          canWrite ? (
            <Button variant="primary" size="md" onClick={() => setCreating(true)}>
              <Plus size={14} weight="bold" aria-hidden />
              New result
            </Button>
          ) : null
        }
      />

      <DataTable
        columns={columns}
        rows={table.rows}
        rowKey={(row) => row.lab_id}
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
        searchPlaceholder="Search test code"
        emptyTitle="No lab results match"
        emptyDescription="Pick a different test or clear the search."
        toolbar={
          <Select
            value={testCode}
            onValueChange={setTestCode}
            options={TEST_OPTIONS}
            size="sm"
            className="w-[13rem]"
            aria-label="Filter by test"
          />
        }
        rowActions={
          canWrite
            ? (row) => (
                <RowActions
                  label={`lab result ${row.lab_id}`}
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
          title={editing ? `Edit lab result ${editing.lab_id}` : 'New lab result'}
          endpoint={ENDPOINT}
          idKey="lab_id"
          invalidateKey={qk.labResults()}
          row={editing ? (editing as unknown as Record<string, unknown>) : undefined}
          fields={formFields}
        />
      ) : null}

      {deleting ? (
        <ConfirmDelete
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          endpoint={ENDPOINT}
          id={deleting.lab_id}
          label={`lab result ${deleting.lab_id}`}
          invalidateKey={qk.labResults()}
        />
      ) : null}
    </div>
  )
}
