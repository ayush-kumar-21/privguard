import { useQuery } from '@tanstack/react-query'

import { fetchAll } from '../lib/api'

/**
 * Options for a small reference table (regions, departments). Bounded to a few
 * pages: a dropdown is the wrong control for a fact table, so anything larger
 * than a reference list is entered by id instead.
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
      const rows = await fetchAll<T>(endpoint, { maxPages: 5 })
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
