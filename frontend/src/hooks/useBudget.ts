import { useQuery } from '@tanstack/react-query'
import axios from 'axios'

import api from '../lib/api'
import { qk } from '../lib/queryKeys'
import type { BudgetAccount, LedgerVerify } from '../lib/types'

/**
 * The caller's budget account. Clinicians and DBAs have no account, so a 404
 * is a normal answer here, not an error: it resolves to null and the topbar
 * meter simply does not render.
 */
export function useBudget(db = 'safehealth') {
  return useQuery({
    queryKey: qk.budget(db),
    queryFn: async (): Promise<BudgetAccount | null> => {
      try {
        const { data } = await api.get<BudgetAccount>('/dp/budget/', { params: { db } })
        return data
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 404) return null
        throw error
      }
    },
    staleTime: 10_000,
  })
}

export function useLedgerVerify(db = 'safehealth') {
  return useQuery({
    queryKey: qk.ledgerVerify(db),
    queryFn: async (): Promise<LedgerVerify | null> => {
      try {
        const { data } = await api.get<LedgerVerify>('/dp/ledger/verify/', { params: { db } })
        return data
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 404) return null
        throw error
      }
    },
    staleTime: 30_000,
  })
}
