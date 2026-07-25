import { useQuery } from '@tanstack/react-query'

import api from '../lib/api'
import type { Paginated } from '../lib/types'

/**
 * Options for a small reference table (regions, departments). Only used where
 * the whole table fits in one page; larger foreign keys are entered by id, so
 * the UI never quietly pages through a fact table to build a dropdown.
 */
export function useOptions<T extends Record<string, unknown>>({
  endpoint,
  valueKey,
  labelKey,
  queryKey,
}: {
  endpoint: string
  valueKey: string
  labelKey: string
  queryKey: readonly unknown[]
}) {
  return useQuery({
    queryKey,
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const { data } = await api.get<Paginated<T> | T[]>(endpoint)
      const rows = Array.isArray(data) ? data : data.results
      return rows.map((row) => ({
        value: String(row[valueKey]),
        label: String(row[labelKey]),
      }))
    },
  })
}

/** id -> label map for rendering a foreign key in a table cell. */
export function optionLabel(
  options: { value: string; label: string }[] | undefined,
  id: number | null | undefined,
): string {
  if (id === null || id === undefined) return '--'
  return options?.find((option) => option.value === String(id))?.label ?? String(id)
}
