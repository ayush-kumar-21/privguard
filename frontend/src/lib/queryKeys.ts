/** Every react-query key in one place, so invalidation is never a guess. */

export type ListParams = Record<string, string | number | boolean | undefined>

export const qk = {
  me: ['me'] as const,

  budget: (db = 'safehealth') => ['budget', db] as const,
  budgetAccounts: (params?: ListParams) => ['budget-accounts', params ?? {}] as const,
  ledgerVerify: (db = 'safehealth') => ['ledger-verify', db] as const,

  queries: (params?: ListParams) => ['queries', params ?? {}] as const,

  patients: (params?: ListParams) => ['patients', params ?? {}] as const,
  visits: (params?: ListParams) => ['visits', params ?? {}] as const,
  diagnoses: (params?: ListParams) => ['diagnoses', params ?? {}] as const,
  labResults: (params?: ListParams) => ['lab-results', params ?? {}] as const,
  regions: () => ['regions'] as const,
  departments: () => ['departments'] as const,

  roles: () => ['roles'] as const,
  columnGrants: (params?: ListParams) => ['column-grants', params ?? {}] as const,
  columnPolicies: () => ['column-policies'] as const,
  tablePolicies: () => ['table-policies'] as const,
  policyGrid: () => ['policy-grid'] as const,
  explain: (sql: string) => ['explain', sql] as const,
} as const

/** Resource name -> the key factory used by its list screen. */
export const resourceKeys = {
  patients: qk.patients,
  visits: qk.visits,
  diagnoses: qk.diagnoses,
  'lab-results': qk.labResults,
} as const

export type ResourceName = keyof typeof resourceKeys
