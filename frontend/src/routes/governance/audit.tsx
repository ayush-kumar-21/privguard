import { useState } from 'react'

import { DataTable, type Column } from '../../components/data-table/DataTable'
import { useServerTable } from '../../components/data-table/useServerTable'
import { StatusChip } from '../../components/privacy/StatusChip'
import { PageHeader } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import { Tooltip } from '../../components/ui/misc'
import { ms, oneLine, relative, rho } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import { useDebounced } from '../../lib/useDebounced'
import type { QueryAudit } from '../../lib/types'
import { useAuth } from '../../stores/auth'

const STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'executed', label: 'executed' },
  { value: 'cache_hit', label: 'cache hit' },
  { value: 'rejected_budget', label: 'rejected: budget' },
  { value: 'rejected_policy', label: 'rejected: policy' },
  { value: 'pending', label: 'pending' },
]

export default function AuditPage() {
  const seesEveryone = useAuth((state) => state.hasRole('auditor', 'dba'))
  const [searchInput, setSearchInput] = useState('')
  const [status, setStatus] = useState('')

  const table = useServerTable<QueryAudit>({
    endpoint: '/queries/',
    keyFactory: qk.queries,
    initialSort: { field: 'submitted_at', desc: true },
    search: useDebounced(searchInput),
    filters: { status },
  })

  const columns: Column<QueryAudit>[] = [
    {
      key: 'query_id',
      header: 'id',
      skeletonWidth: 'w-10',
      render: (row) => <span className="num text-faint">{row.query_id}</span>,
    },
    {
      key: 'status',
      header: 'status',
      skeletonWidth: 'w-20',
      render: (row) => <StatusChip status={row.status} />,
    },
    {
      key: 'principal_name',
      header: 'principal',
      skeletonWidth: 'w-16',
      render: (row) => <span className="num text-muted">{row.principal_name}</span>,
    },
    {
      key: 'sql_text',
      header: 'sql',
      className: 'min-w-[24rem]',
      skeletonWidth: 'w-72',
      render: (row) => (
        <Tooltip content={<span className="num whitespace-pre-wrap">{row.sql_text}</span>}>
          <code className="block max-w-[34rem] cursor-help truncate text-micro text-muted">
            {oneLine(row.sql_text)}
          </code>
        </Tooltip>
      ),
    },
    {
      key: 'rho_charged',
      header: 'rho',
      sortable: true,
      align: 'right',
      skeletonWidth: 'w-12',
      render: (row) => (
        <span className={row.rho_charged > 0 ? 'num' : 'num text-faint'}>
          {rho(row.rho_charged)}
        </span>
      ),
    },
    {
      key: 'latency_ms',
      header: 'latency',
      sortable: true,
      align: 'right',
      skeletonWidth: 'w-12',
      render: (row) => <span className="num text-muted">{ms(row.latency_ms)}</span>,
    },
    {
      key: 'submitted_at',
      header: 'submitted',
      sortable: true,
      align: 'right',
      skeletonWidth: 'w-20',
      render: (row) => <span className="num text-muted">{relative(row.submitted_at)}</span>,
    },
  ]

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Audit"
        description={
          seesEveryone
            ? 'Every query the gateway has seen, across all principals, with what it cost and whether it was refused.'
            : 'Every query you have run, with what it cost and whether it was refused. The server scopes this list to you.'
        }
      />

      <DataTable
        columns={columns}
        rows={table.rows}
        rowKey={(row) => row.query_id}
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
        searchPlaceholder="Search SQL text"
        emptyTitle="No queries match"
        emptyDescription="Clear the status filter, or run something in the console."
        toolbar={
          <Select
            value={status}
            onValueChange={setStatus}
            options={STATUS_OPTIONS}
            size="sm"
            className="w-[12rem]"
            aria-label="Filter by status"
          />
        }
      />
    </div>
  )
}
