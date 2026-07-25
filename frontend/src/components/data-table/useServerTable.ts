import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import api from '../../lib/api'
import type { ListParams } from '../../lib/queryKeys'
import type { Paginated } from '../../lib/types'

export const PAGE_SIZE = 20

export interface SortState {
  field: string
  desc: boolean
}

/**
 * Drives one DRF-backed list: page, ordering, search and filters map straight
 * to `?page=`, `?ordering=`, `?search=` and the viewset's filterset fields.
 * Nothing is sorted or paged in the browser.
 */
export function useServerTable<T>({
  endpoint,
  keyFactory,
  initialSort,
  filters = {},
  search = '',
  enabled = true,
}: {
  endpoint: string
  keyFactory: (params?: ListParams) => readonly unknown[]
  initialSort?: SortState
  /** Filter values keyed by the DRF field name. Empty strings are dropped. */
  filters?: Record<string, string>
  search?: string
  enabled?: boolean
}) {
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<SortState | undefined>(initialSort)

  const activeFilters = useMemo(
    () => Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '')),
    [filters],
  )
  const filterSignature = JSON.stringify(activeFilters)

  // Any change to what is being asked for resets to the first page.
  useEffect(() => {
    setPage(1)
  }, [filterSignature, search])

  const params: ListParams = useMemo(() => {
    const next: ListParams = { page, ...activeFilters }
    if (search) next.search = search
    if (sort) next.ordering = `${sort.desc ? '-' : ''}${sort.field}`
    return next
    // filterSignature stands in for activeFilters identity
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filterSignature, search, sort])

  const query = useQuery({
    queryKey: keyFactory(params),
    queryFn: async () => {
      const { data } = await api.get<Paginated<T>>(endpoint, { params })
      return data
    },
    placeholderData: keepPreviousData,
    enabled,
  })

  const count = query.data?.count ?? 0
  const pageCount = Math.max(1, Math.ceil(count / PAGE_SIZE))

  function toggleSort(field: string) {
    setSort((current) =>
      current?.field === field
        ? current.desc
          ? undefined // third click clears back to the server default order
          : { field, desc: true }
        : { field, desc: false },
    )
    setPage(1)
  }

  return {
    rows: query.data?.results ?? [],
    count,
    page,
    pageCount,
    setPage,
    sort,
    toggleSort,
    params,
    query,
    /** True only for the very first load, so the toolbar does not blink. */
    isInitialLoading: query.isPending,
    isFetching: query.isFetching,
  }
}
