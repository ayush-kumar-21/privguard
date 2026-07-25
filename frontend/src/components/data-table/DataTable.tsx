import {
  ArrowDown,
  ArrowUp,
  ArrowsDownUp,
  CaretLeft,
  CaretRight,
  MagnifyingGlass,
  Table as TableIcon,
} from '@phosphor-icons/react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { useMemo, type ReactNode } from 'react'

import { apiError } from '../../lib/api'
import { cn } from '../../lib/cn'
import { int } from '../../lib/format'
import { EmptyState, ErrorState, SkeletonRows } from '../states'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../ui/table'
import { PAGE_SIZE, type SortState } from './useServerTable'

/**
 * Column description in the app's own terms. It is compiled to a TanStack
 * `ColumnDef` below; screens never touch the table library directly.
 */
export interface Column<T> {
  /** DRF field name. Also the `?ordering=` key when `sortable` is set. */
  key: string
  header: string
  render: (row: T) => ReactNode
  sortable?: boolean
  align?: 'left' | 'right'
  /** Skeleton width for this column so loading rows match the real layout. */
  skeletonWidth?: string
  className?: string
}

interface ColumnMeta {
  align?: 'left' | 'right'
  className?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string | number
  count: number
  page: number
  pageCount: number
  onPageChange: (page: number) => void
  sort?: SortState
  onToggleSort: (field: string) => void
  isInitialLoading: boolean
  isFetching?: boolean
  error?: unknown
  onRetry?: () => void
  emptyTitle?: string
  emptyDescription?: string
  /** Search box: omit `onSearchChange` to hide it. */
  search?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
  /** Filter controls and primary action, rendered right of the search box. */
  toolbar?: ReactNode
  rowActions?: (row: T) => ReactNode
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  count,
  page,
  pageCount,
  onPageChange,
  sort,
  onToggleSort,
  isInitialLoading,
  isFetching,
  error,
  onRetry,
  emptyTitle = 'Nothing here yet',
  emptyDescription,
  search,
  onSearchChange,
  searchPlaceholder = 'Search',
  toolbar,
  rowActions,
}: DataTableProps<T>) {
  const columnDefs = useMemo<ColumnDef<T>[]>(() => {
    const defs: ColumnDef<T>[] = columns.map((column) => ({
      id: column.key,
      header: column.header,
      cell: ({ row }) => column.render(row.original),
      enableSorting: Boolean(column.sortable),
      meta: { align: column.align, className: column.className } satisfies ColumnMeta,
    }))
    if (rowActions) {
      defs.push({
        id: '__actions',
        header: 'actions',
        cell: ({ row }) => rowActions(row.original),
        enableSorting: false,
        meta: { align: 'right', className: 'w-px' } satisfies ColumnMeta,
      })
    }
    return defs
  }, [columns, rowActions])

  const table = useReactTable({
    data: rows,
    columns: columnDefs,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(rowKey(row)),
    // The server owns paging, sorting and filtering; the table only renders.
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    pageCount,
  })

  const from = count === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, count)

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      {onSearchChange || toolbar ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          {onSearchChange ? (
            <div className="relative min-w-[180px] flex-1 sm:max-w-[280px]">
              <MagnifyingGlass
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
                aria-hidden
              />
              <Input
                value={search ?? ''}
                onChange={(event) => onSearchChange(event.target.value)}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                className="pl-8"
              />
            </div>
          ) : null}
          {toolbar ? (
            <div className="flex flex-wrap items-center gap-2">{toolbar}</div>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <ErrorState
          message={apiError(error, 'The list could not be loaded.')}
          onRetry={onRetry}
        />
      ) : (
        <>
          <TableScroll>
            <Table>
              <THead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => {
                      const meta = header.column.columnDef.meta as ColumnMeta | undefined
                      const active = sort?.field === header.column.id
                      const label = flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )
                      return (
                        <TH
                          key={header.id}
                          className={cn(
                            meta?.align === 'right' && 'text-right',
                            meta?.className,
                          )}
                          aria-sort={
                            active ? (sort.desc ? 'descending' : 'ascending') : undefined
                          }
                        >
                          {header.column.getCanSort() ? (
                            <button
                              type="button"
                              onClick={() => onToggleSort(header.column.id)}
                              className={cn(
                                'inline-flex items-center gap-1 rounded-[4px] transition-colors hover:text-text',
                                active && 'text-accent',
                                meta?.align === 'right' && 'flex-row-reverse',
                              )}
                            >
                              {label}
                              {active ? (
                                sort.desc ? (
                                  <ArrowDown size={11} weight="bold" aria-hidden />
                                ) : (
                                  <ArrowUp size={11} weight="bold" aria-hidden />
                                )
                              ) : (
                                <ArrowsDownUp size={11} className="opacity-40" aria-hidden />
                              )}
                            </button>
                          ) : (
                            label
                          )}
                        </TH>
                      )
                    })}
                  </tr>
                ))}
              </THead>

              <TBody>
                {isInitialLoading ? (
                  <SkeletonRows
                    rows={8}
                    widths={[
                      ...columns.map((column) => column.skeletonWidth ?? 'w-20'),
                      ...(rowActions ? ['w-10'] : []),
                    ]}
                  />
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={columnDefs.length}>
                      <EmptyState
                        icon={TableIcon}
                        title={emptyTitle}
                        description={emptyDescription}
                      />
                    </td>
                  </tr>
                ) : (
                  table.getRowModel().rows.map((row) => (
                    <TR key={row.id}>
                      {row.getVisibleCells().map((cell) => {
                        const meta = cell.column.columnDef.meta as ColumnMeta | undefined
                        return (
                          <TD
                            key={cell.id}
                            className={cn(
                              meta?.align === 'right' && 'text-right',
                              meta?.className,
                            )}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TD>
                        )
                      })}
                    </TR>
                  ))
                )}
              </TBody>
            </Table>
          </TableScroll>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-2.5">
            <p className="num text-micro text-muted" aria-live="polite">
              {isInitialLoading ? (
                <span className="text-faint">loading</span>
              ) : (
                <>
                  showing {int(from)}-{int(to)} of {int(count)}
                  {isFetching ? <span className="ml-2 text-faint">updating</span> : null}
                </>
              )}
            </p>

            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="secondary"
                disabled={page <= 1 || isInitialLoading}
                onClick={() => onPageChange(page - 1)}
                aria-label="Previous page"
              >
                <CaretLeft size={12} weight="bold" aria-hidden />
                Prev
              </Button>
              <span className="num px-1.5 text-micro text-muted">
                {int(page)} / {int(pageCount)}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={page >= pageCount || isInitialLoading}
                onClick={() => onPageChange(page + 1)}
                aria-label="Next page"
              >
                Next
                <CaretRight size={12} weight="bold" aria-hidden />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
