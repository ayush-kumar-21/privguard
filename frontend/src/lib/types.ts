/** Shapes returned by the PrivGuard API. Mirrors backend/api/serializers.py. */

export type RoleName = 'dp_analyst' | 'clinician' | 'auditor' | 'dba'

export interface Principal {
  id: number
  username: string
  kind: 'human' | 'service'
  is_active: boolean
  is_staff: boolean
  roles: RoleName[]
  date_joined: string
}

export interface BudgetAccount {
  account_id: number
  principal_name: string
  rho_total: number
  rho_spent: number
  rho_reserved: number
  rho_remaining: number
  delta_target: number | null
  window_type: string
  status: string
  /** Run quota set by the administrator; null = unlimited. */
  max_queries: number | null
  queries_used: number
  queries_remaining: number | null
}

export type QueryStatus =
  | 'executed'
  | 'cache_hit'
  | 'pending'
  | 'rejected_budget'
  | 'rejected_policy'

export interface QueryAudit {
  query_id: number
  principal_name: string
  status: QueryStatus
  rho_charged: number
  /** Present only for dba/auditor (timing side channel). */
  latency_ms?: number | null
  delta_charged?: number
  sql_text: string
  submitted_at: string
}

/** One row of a DP answer: `{ value }` for scalars, `{ <group_col>, value }` grouped. */
export type DPRow = Record<string, string | number>

export interface DPQueryResponse {
  status: QueryStatus
  result: DPRow[]
  rho_charged: number
  epsilon_charged: number
  mechanism: string
  query_id: number
  suppressed_groups?: number
  /** Why a `rejected_budget` was refused: 'quota' (runs used up) or 'global_cap'. */
  reason?: string
  budget: {
    rho_total: number
    rho_spent: number
    rho_reserved: number
    rho_remaining: number
    status: string
    max_queries: number | null
    queries_used: number
    queries_remaining: number | null
  }
}

export interface LedgerVerify {
  ok: boolean
  entries: number
  first_bad_entry: number | null
}

export interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

// --- SafeHealth application data ------------------------------------------
export interface Patient {
  patient_id: number
  region: number | null
  birth_year: number
  age: number
  sex: 'M' | 'F' | string
  zip3: string
  insurance_type: string
  enrolled_at: string
}

export interface Visit {
  visit_id: number
  patient: number
  dept: number | null
  attending_staff: number | null
  admit_ts: string
  discharge_ts: string | null
  length_of_stay_days: number | null
  total_charge: number
  admission_type: string
}

export interface Diagnosis {
  diagnosis_id: number
  visit: number
  icd10_code: string
  is_primary: boolean
  severity: number
}

export interface LabResult {
  lab_id: number
  visit: number
  test_code: string
  value_num: number
  unit: string
  taken_at: string
}

export interface Region {
  region_id: number
  region_name: string
  state_code: string
}

// --- Policy / access control ----------------------------------------------
export type AccessMode = 'raw' | 'dp' | 'redacted' | 'denied'

export interface Role {
  role_id: number
  role_name: RoleName
  description: string | null
}

export interface ColumnGrant {
  grant_id: number
  column: number
  role: number
  access_mode: AccessMode
  rho_multiplier: number
  abe_policy_expr: string | null
}

export type Mechanism = 'laplace' | 'gaussian' | 'discrete_laplace' | 'none'

export interface ColumnPrivacy {
  policy_id: number
  column: number
  mechanism: Mechanism
  lower_clamp: number | null
  upper_clamp: number | null
  max_contrib_per_unit: number
  is_groupable: boolean
  is_filterable: boolean
  min_partition_size: number
}

export interface TablePrivacy {
  table: number
  privacy_unit_col: number
  max_rows_per_unit: number
  max_groups_per_unit: number
  enforce_dp: boolean
  min_partition_rho: number
}

// --- Policy grid (gateway mode) ---------------------------------------------
export interface PolicyColumn {
  table: string
  column: string
  type: string
  access: 'allowed' | 'aggregate_only' | 'denied'
  filterable: boolean
  groupable: boolean
  mechanism: string | null
  lower_clamp: number | null
  upper_clamp: number | null
  /** role name -> may that role reference this column */
  grants: Record<string, boolean>
}

export interface PolicyGrid {
  roles: string[]
  columns: PolicyColumn[]
}

// --- Plan viewer (gateway mode) ---------------------------------------------
export interface PlanExplain {
  fingerprint: string
  cached: boolean
  logical: string[]
  logical_text: string
  /** Physical operators in execution order, e.g. Scan, Filter, ContributionBound, ... */
  physical: string[]
  mechanism: string
  sensitivity: number
  privacy_unit: string
}
