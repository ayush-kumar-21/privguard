# PrivGuard — Data Dictionary

Generated from the live `safehealth` PostgreSQL database. Every table below is
derived from the ER model in `PrivGuard_ER_Documentation.md`.

**Legend** — `PK` primary key · `FK` foreign key · `U` unique · `NN` not null

| Group | Tables |
|---|---|
| Clinical dataset (SafeHealth) | 8 |
| Governance catalog (`sys_*`) | 24 |
| Framework / auth internals | 9 |
| **Total** | **41** |

---

## 1. Clinical dataset (SafeHealth)

The data being protected. `patients.patient_id` is the **privacy unit**: every differential-privacy guarantee is stated per patient.

### `departments`  — 5 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `dept_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `dept_name` | `varchar(64)` | U | NN | — |
| 3 | `specialty` | `varchar(64)` | — |  | — |
| 4 | `hospital_id` | `integer` | FK→`hospitals.hospital_id` U | NN | — |

**Indexes** (3 total, 2 secondary)

- `departments_hospital_id_74db31cd` — btree (hospital_id)
- `uq_dept_per_hospital` — UNIQUE btree (hospital_id, dept_name)

### `diagnoses`  — 1,206 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `diagnosis_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `icd10_code` | `varchar(10)` | — | NN | — |
| 3 | `is_primary` | `boolean` | — | NN | — |
| 4 | `severity` | `integer` | — |  | — |
| 5 | `visit_id` | `bigint` | FK→`visits.visit_id` | NN | — |

**Check constraints**

- `ck_diagnosis_severity_range` — `(((severity IS NULL) OR ((severity >= 1) AND (severity <= 5))))`

**Indexes** (4 total, 3 secondary)

- `diagnoses_visit_id_846f26d9` — btree (visit_id)
- `idx_diag_icd10` — btree (icd10_code)
- `idx_diag_visit_primary` — btree (visit_id, is_primary)

### `hospitals`  — 4 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `hospital_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `hospital_name` | `varchar(128)` | — | NN | — |
| 3 | `bed_count` | `integer` | — | NN | — |
| 4 | `region_id` | `integer` | FK→`regions.region_id` | NN | — |

**Check constraints**

- `ck_hospital_bed_count_pos` — `((bed_count > 0))`

**Indexes** (2 total, 1 secondary)

- `hospitals_region_id_5e198cac` — btree (region_id)

### `lab_results`  — 639 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `lab_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `test_code` | `varchar(16)` | — | NN | — |
| 3 | `value_num` | `float8` | — |  | — |
| 4 | `unit` | `varchar(16)` | — |  | — |
| 5 | `taken_at` | `timestamptz` | — | NN | — |
| 6 | `visit_id` | `bigint` | FK→`visits.visit_id` | NN | — |

**Indexes** (4 total, 3 secondary)

- `idx_lab_taken_at` — btree (taken_at)
- `idx_lab_visit_test` — btree (visit_id, test_code)
- `lab_results_visit_id_200adc75` — btree (visit_id)

### `patients`  — 150 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `patient_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `mrn` | `varchar(32)` | U | NN | — |
| 3 | `birth_year` | `integer` | — | NN | — |
| 4 | `sex` | `varchar(8)` | — | NN | — |
| 5 | `zip3` | `varchar(3)` | — | NN | — |
| 6 | `insurance_type` | `varchar(24)` | — | NN | — |
| 7 | `enrolled_at` | `date` | — | NN | — |
| 8 | `region_id` | `integer` | FK→`regions.region_id` | NN | — |

**Check constraints**

- `ck_patient_birth_year_range` — `(((birth_year >= 1900) AND (birth_year <= 2100)))`

**Indexes** (5 total, 4 secondary)

- `idx_patients_insurance` — btree (insurance_type)
- `idx_patients_zip3` — btree (zip3)
- `patients_region_id_7008c210` — btree (region_id)
- `uq_patient_mrn` — UNIQUE btree (mrn)

### `regions`  — 3 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `region_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `region_name` | `varchar(64)` | U | NN | — |
| 3 | `state_code` | `varchar(4)` | — | NN | — |

**Indexes** (2 total, 1 secondary)

- `uq_region_name` — UNIQUE btree (region_name)

### `staff`  — 30 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `staff_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `full_name` | `varchar(128)` | — | NN | — |
| 3 | `staff_role` | `varchar(32)` | — | NN | — |
| 4 | `hire_date` | `date` | — | NN | — |
| 5 | `dept_id` | `integer` | FK→`departments.dept_id` | NN | — |

**Indexes** (2 total, 1 secondary)

- `staff_dept_id_a62f379d` — btree (dept_id)

### `visits`  — 600 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `visit_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `admit_ts` | `timestamptz` | — | NN | — |
| 3 | `discharge_ts` | `timestamptz` | — |  | — |
| 4 | `total_charge` | `float8` | — | NN | — |
| 5 | `admission_type` | `varchar(16)` | — | NN | — |
| 6 | `attending_staff_id` | `integer` | FK→`staff.staff_id` | NN | — |
| 7 | `dept_id` | `integer` | FK→`departments.dept_id` | NN | — |
| 8 | `patient_id` | `integer` | FK→`patients.patient_id` | NN | — |

**Check constraints**

- `ck_visit_charge_nonneg` — `((total_charge >= (0.0)::double precision))`
- `ck_visit_discharge_after_admit` — `(((discharge_ts IS NULL) OR (discharge_ts >= admit_ts)))`

**Indexes** (10 total, 9 secondary)

- `idx_visits_admission_type` — btree (admission_type)
- `idx_visits_admit` — btree (admit_ts)
- `idx_visits_charge` — btree (total_charge)
- `idx_visits_dept_admit` — btree (dept_id, admit_ts)
- `idx_visits_patient` — btree (patient_id)
- `idx_visits_patient_vid` — btree (patient_id, visit_id)
- `visits_attending_staff_id_6e379cfa` — btree (attending_staff_id)
- `visits_dept_id_d38cf2a4` — btree (dept_id)
- …and 1 more

## 2. Governance catalog (`sys_*`)

PrivGuard's own control plane — schema catalog, privacy policy, principals and roles, budget accounts and the append-only ledger, and the audit trail.

### `sys_attribute`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `attr_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `attr_name` | `varchar(64)` | U | NN | — |

**Indexes** (3 total, 1 secondary)

- `sys_attribute_attr_name_ea26903f_like` — btree (attr_name varchar_pattern_ops)

### `sys_budget_account`  — 3 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `account_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `rho_total` | `float8` | — | NN | — |
| 3 | `rho_spent` | `float8` | — | NN | — |
| 4 | `rho_reserved` | `float8` | — | NN | — |
| 5 | `delta_target` | `float8` | — | NN | — |
| 6 | `window_type` | `varchar(12)` | — | NN | — |
| 7 | `window_start` | `timestamptz` | — | NN | — |
| 8 | `refill_period_s` | `bigint` | — |  | — |
| 9 | `status` | `varchar(12)` | — | NN | — |
| 10 | `db_id` | `integer` | FK→`sys_database.db_id` U | NN | — |
| 11 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |

**Check constraints**

- `ck_account_delta_in_unit_interval` — `(((delta_target > (0.0)::double precision) AND (delta_target < (1.0)::double precision)))`
- `ck_account_never_oversold` — `((rho_spent <= (rho_total - rho_reserved)))`
- `ck_account_refill_needs_period` — `(((NOT ((window_type)::text = 'refill'::text)) OR (refill_period_s > 0)))`
- `ck_account_rho_reserved_nonneg` — `((rho_reserved >= (0.0)::double precision))`
- `ck_account_rho_spent_nonneg` — `((rho_spent >= (0.0)::double precision))`
- `ck_account_rho_total_pos` — `((rho_total > (0.0)::double precision))`

**Indexes** (4 total, 3 secondary)

- `sys_budget_account_db_id_af655112` — btree (db_id)
- `sys_budget_account_principal_id_a73f8a0e` — btree (principal_id)
- `uq_account_principal_db` — UNIQUE btree (principal_id, db_id)

### `sys_patient_budget`  — one row per patient

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `patient_id` | `integer` | PK, FK→`patients.patient_id` | NN | — |
| 2 | `rho_total` | `float8` | — | NN | — |
| 3 | `rho_spent` | `float8` | — | NN | `0` |
| 4 | `status` | `varchar(12)` | — | NN | `'active'` |

**Check constraints**

- `ck_patient_rho_total_pos` — `rho_total > 0`
- `ck_patient_rho_spent_nonneg` — `rho_spent >= 0`
- `ck_patient_never_oversold` — `rho_spent <= rho_total`

**Triggers / routines.** Rows are created by `trg_patient_budget_init` (AFTER INSERT ON `patients`); spending moves only through `fn_charge_patients` and `fn_refund_patients`.

### `sys_patient_ledger`  — one row per charge or refund

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `entry_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `patient_id` | `integer` | FK→`patients.patient_id` | NN | — |
| 3 | `query_id` | `bigint` | FK→`sys_query.query_id` |  | — |
| 4 | `op` | `varchar(10)` | — | NN | — |
| 5 | `rho_delta` | `float8` | — | NN | — |
| 6 | `rho_after` | `float8` | — | NN | — |
| 7 | `logged_at` | `timestamptz` | — | NN | — |
| 8 | `prev_hash` | `bytea` | — | NN | — |
| 9 | `entry_hash` | `bytea` | — | NN | — |

**Triggers.** `trg_patient_ledger_hash` (BEFORE INSERT, per-patient SHA-256 chain), `trg_patient_ledger_readonly` (BEFORE UPDATE/DELETE: append-only), `trg_patient_ledger_no_truncate`.

**Indexes:** `idx_patient_ledger` — btree (patient_id, entry_id); the primary key.

**View `v_patient_budget`** — per patient: allowance, spent, remaining, status, number of charges, last charge time. Verified by `fn_verify_patient_chain(patient_id)`.

### `sys_budget_ledger`  — 40 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `entry_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `op` | `varchar(10)` | — | NN | — |
| 3 | `rho_delta` | `float8` | — | NN | — |
| 4 | `rho_after` | `float8` | — | NN | — |
| 5 | `eps_equiv` | `float8` | — | NN | — |
| 6 | `mechanism` | `varchar(20)` | — | NN | — |
| 7 | `sensitivity` | `float8` | — | NN | — |
| 8 | `sigma_or_scale` | `float8` | — | NN | — |
| 9 | `logged_at` | `timestamptz` | — | NN | — |
| 10 | `prev_hash` | `bytea` | — | NN | — |
| 11 | `entry_hash` | `bytea` | — | NN | — |
| 12 | `account_id` | `integer` | FK→`sys_budget_account.account_id` | NN | — |
| 13 | `query_id` | `bigint` | FK→`sys_query.query_id` |  | — |

**Check constraints**

- `ck_ledger_hash_len` — `(((octet_length(prev_hash) = 32) AND (octet_length(entry_hash) = 32)))`

**Indexes** (8 total, 7 secondary)

- `idx_ledger_account` — btree (account_id, entry_id)
- `idx_ledger_logged_at` — btree (logged_at)
- `idx_ledger_query_id` — btree (query_id)
- `sys_budget_ledger_account_id_e66ade5c` — btree (account_id)
- `sys_budget_ledger_query_id_9c96146f` — btree (query_id)
- `uq_ledger_reserve_once` — UNIQUE btree (query_id) WHERE ((op)::text = 'RESERVE'::text)
- `uq_ledger_settle_once` — UNIQUE btree (query_id) WHERE ((op)::text = ANY ((ARRAY['COMMIT'::character varying, 'ABORT'::character varying])::text[]))

### `sys_column`  — 6 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `column_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `col_name` | `varchar(64)` | U | NN | — |
| 3 | `ordinal` | `integer` | U | NN | — |
| 4 | `data_type` | `varchar(24)` | — | NN | — |
| 5 | `type_len` | `integer` | — |  | — |
| 6 | `is_nullable` | `boolean` | — | NN | — |
| 7 | `is_privacy_unit` | `boolean` | — | NN | — |
| 8 | `is_encrypted` | `boolean` | — | NN | — |
| 9 | `table_id` | `integer` | FK→`sys_table.table_id` U | NN | — |

**Check constraints**

- `ck_column_ordinal_pos` — `((ordinal >= 1))`

**Indexes** (4 total, 3 secondary)

- `sys_column_table_id_56b3cadd` — btree (table_id)
- `uq_col_table_name` — UNIQUE btree (table_id, col_name)
- `uq_col_table_ordinal` — UNIQUE btree (table_id, ordinal)

### `sys_column_grant`  — 2 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `grant_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `access_mode` | `varchar(12)` | — | NN | — |
| 3 | `rho_multiplier` | `float8` | — | NN | — |
| 4 | `abe_policy_expr` | `varchar(255)` | — |  | — |
| 5 | `column_id` | `integer` | FK→`sys_column.column_id` U | NN | — |
| 6 | `role_id` | `integer` | FK→`sys_role.role_id` U | NN | — |

**Check constraints**

- `ck_grant_rho_multiplier_pos` — `((rho_multiplier > (0.0)::double precision))`

**Indexes** (4 total, 3 secondary)

- `sys_column_grant_column_id_956412d4` — btree (column_id)
- `sys_column_grant_role_id_340d8fa2` — btree (role_id)
- `uq_column_role_grant` — UNIQUE btree (column_id, role_id)

### `sys_column_privacy`  — 1 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `policy_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `mechanism` | `varchar(20)` | — | NN | — |
| 3 | `lower_clamp` | `float8` | — |  | — |
| 4 | `upper_clamp` | `float8` | — |  | — |
| 5 | `max_contrib_per_unit` | `integer` | — | NN | — |
| 6 | `is_groupable` | `boolean` | — | NN | — |
| 7 | `is_filterable` | `boolean` | — | NN | — |
| 8 | `min_partition_size` | `integer` | — | NN | — |
| 9 | `column_id` | `integer` | FK→`sys_column.column_id` U | NN | — |

**Check constraints**

- `ck_colpriv_contrib_pos` — `((max_contrib_per_unit >= 1))`
- `ck_colpriv_min_partition_nonneg` — `((min_partition_size >= 0))`

### `sys_database`  — 1 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `db_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `db_name` | `varchar(64)` | U | NN | — |
| 3 | `dp_model` | `varchar(16)` | — | NN | — |
| 4 | `default_rho` | `float8` | — | NN | — |
| 5 | `default_delta` | `float8` | — | NN | — |
| 6 | `created_at` | `timestamptz` | — | NN | — |

**Indexes** (3 total, 1 secondary)

- `sys_database_db_name_125f15cc_like` — btree (db_name varchar_pattern_ops)

### `sys_index`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `index_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `index_name` | `varchar(64)` | U | NN | — |
| 3 | `root_page_id` | `integer` | — | NN | — |
| 4 | `index_type` | `varchar(16)` | — | NN | — |
| 5 | `is_unique` | `boolean` | — | NN | — |
| 6 | `height` | `integer` | — | NN | — |
| 7 | `table_id` | `integer` | FK→`sys_table.table_id` | NN | — |

**Indexes** (4 total, 2 secondary)

- `sys_index_index_name_a8a6353c_like` — btree (index_name varchar_pattern_ops)
- `sys_index_table_id_f6e26c12` — btree (table_id)

### `sys_index_column`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `ordinal` | `integer` | U | NN | — |
| 3 | `column_id` | `integer` | FK→`sys_column.column_id` | NN | — |
| 4 | `index_id` | `integer` | FK→`sys_index.index_id` U | NN | — |

**Indexes** (4 total, 3 secondary)

- `pk_index_column` — UNIQUE btree (index_id, ordinal)
- `sys_index_column_column_id_10684efa` — btree (column_id)
- `sys_index_column_index_id_630b8fe7` — btree (index_id)

### `sys_key_material`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `key_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `algo` | `varchar(24)` | — | NN | — |
| 3 | `wrapped_dek` | `bytea` | — | NN | — |
| 4 | `abe_policy_expr` | `varchar(255)` | — |  | — |
| 5 | `key_version` | `integer` | — | NN | — |
| 6 | `created_at` | `timestamptz` | — | NN | — |
| 7 | `column_id` | `integer` | FK→`sys_column.column_id` | NN | — |

### `sys_noise_cache`  — 20 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `cache_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `noise_seed` | `bytea` | — | NN | — |
| 3 | `cached_result` | `jsonb` | — | NN | — |
| 4 | `rho_charged` | `float8` | — | NN | — |
| 5 | `created_at` | `timestamptz` | — | NN | — |
| 6 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |
| 7 | `fingerprint_id` | `bigint` | FK→`sys_query_fingerprint.fingerprint_id` U | NN | — |

**Indexes** (4 total, 3 secondary)

- `sys_noise_cache_fingerprint_id_48dd39e1` — btree (fingerprint_id)
- `sys_noise_cache_principal_id_7925df18` — btree (principal_id)
- `uq_noise_cache_fp_principal` — UNIQUE btree (fingerprint_id, principal_id)

### `sys_predicate_overlap`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `overlap_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `jaccard_est` | `float8` | — | NN | — |
| 3 | `symmetric_diff_est` | `bigint` | — | NN | — |
| 4 | `action_taken` | `varchar(20)` | — | NN | — |
| 5 | `detected_at` | `timestamptz` | — | NN | — |
| 6 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |
| 7 | `fp_a` | `bigint` | FK→`sys_query_fingerprint.fingerprint_id` U | NN | — |
| 8 | `fp_b` | `bigint` | FK→`sys_query_fingerprint.fingerprint_id` U | NN | — |

**Check constraints**

- `ck_overlap_canonical_order` — `((fp_a < fp_b))`
- `ck_overlap_jaccard_unit` — `(((jaccard_est >= (0.0)::double precision) AND (jaccard_est <= (1.0)::double precision)))`
- `ck_overlap_symdiff_nonneg` — `((symmetric_diff_est >= 0))`

**Indexes** (5 total, 4 secondary)

- `sys_predicate_overlap_fp_a_6237385e` — btree (fp_a)
- `sys_predicate_overlap_fp_b_ea68fa54` — btree (fp_b)
- `sys_predicate_overlap_principal_id_af22e273` — btree (principal_id)
- `uq_overlap_pair_principal` — UNIQUE btree (fp_a, fp_b, principal_id)

### `sys_principal`  — 5 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `password` | `varchar(128)` | — | NN | — |
| 3 | `last_login` | `timestamptz` | — |  | — |
| 4 | `is_superuser` | `boolean` | — | NN | — |
| 5 | `username` | `varchar(150)` | U | NN | — |
| 6 | `first_name` | `varchar(150)` | — | NN | — |
| 7 | `last_name` | `varchar(150)` | — | NN | — |
| 8 | `email` | `varchar(254)` | — | NN | — |
| 9 | `is_staff` | `boolean` | — | NN | — |
| 10 | `is_active` | `boolean` | — | NN | — |
| 11 | `date_joined` | `timestamptz` | — | NN | — |
| 12 | `kind` | `varchar(12)` | — | NN | — |

**Indexes** (3 total, 1 secondary)

- `sys_principal_username_f47eae28_like` — btree (username varchar_pattern_ops)

### `sys_principal_attribute`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `granted_at` | `timestamptz` | — | NN | — |
| 3 | `expires_at` | `timestamptz` | — |  | — |
| 4 | `attr_id` | `integer` | FK→`sys_attribute.attr_id` U | NN | — |
| 5 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |

**Check constraints**

- `ck_principal_attr_expiry_after_grant` — `(((expires_at IS NULL) OR (expires_at > granted_at)))`

**Indexes** (4 total, 3 secondary)

- `pk_principal_attribute` — UNIQUE btree (principal_id, attr_id)
- `sys_principal_attribute_attr_id_e28e9aeb` — btree (attr_id)
- `sys_principal_attribute_principal_id_7b5b9a87` — btree (principal_id)

### `sys_principal_groups`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |
| 3 | `group_id` | `integer` | FK→`auth_group.id` U | NN | — |

**Indexes** (4 total, 3 secondary)

- `sys_principal_groups_group_id_30d6ef76` — btree (group_id)
- `sys_principal_groups_principal_id_4547a1f3` — btree (principal_id)
- `sys_principal_groups_principal_id_group_id_08741d5d_uniq` — UNIQUE btree (principal_id, group_id)

### `sys_principal_role`  — 5 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `granted_at` | `timestamptz` | — | NN | — |
| 3 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |
| 4 | `role_id` | `integer` | FK→`sys_role.role_id` U | NN | — |

**Indexes** (4 total, 3 secondary)

- `pk_principal_role` — UNIQUE btree (principal_id, role_id)
- `sys_principal_role_principal_id_5031684b` — btree (principal_id)
- `sys_principal_role_role_id_e51f45a5` — btree (role_id)

### `sys_principal_user_permissions`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `principal_id` | `bigint` | FK→`sys_principal.id` U | NN | — |
| 3 | `permission_id` | `integer` | FK→`auth_permission.id` U | NN | — |

**Indexes** (4 total, 3 secondary)

- `sys_principal_user_permi_principal_id_permission__bed02aea_uniq` — UNIQUE btree (principal_id, permission_id)
- `sys_principal_user_permissions_permission_id_7f9d59c6` — btree (permission_id)
- `sys_principal_user_permissions_principal_id_e2640197` — btree (principal_id)

### `sys_query`  — 25 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `query_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `sql_text` | `varchar(4000)` | — | NN | — |
| 3 | `plan_json` | `jsonb` | — |  | — |
| 4 | `status` | `varchar(24)` | — | NN | — |
| 5 | `rho_charged` | `float8` | — | NN | — |
| 6 | `rows_scanned` | `bigint` | — |  | — |
| 7 | `latency_ms` | `float8` | — |  | — |
| 8 | `submitted_at` | `timestamptz` | — | NN | — |
| 9 | `db_id` | `integer` | FK→`sys_database.db_id` | NN | — |
| 10 | `principal_id` | `bigint` | FK→`sys_principal.id` | NN | — |
| 11 | `fingerprint_id` | `bigint` | FK→`sys_query_fingerprint.fingerprint_id` |  | — |

**Check constraints**

- `ck_query_rho_nonneg` — `((rho_charged >= (0.0)::double precision))`

**Indexes** (7 total, 6 secondary)

- `idx_query_pending` — btree (submitted_at) WHERE ((status)::text = 'pending'::text)
- `idx_query_principal` — btree (principal_id, submitted_at)
- `idx_query_status_submitted` — btree (status, submitted_at DESC)
- `sys_query_db_id_ca3a5184` — btree (db_id)
- `sys_query_fingerprint_id_5ebf8978` — btree (fingerprint_id)
- `sys_query_principal_id_590022de` — btree (principal_id)

### `sys_query_fingerprint`  — 16 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `fingerprint_id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `ast_hash` | `bytea` | U | NN | — |
| 3 | `agg_kind` | `varchar(12)` | — | NN | — |
| 4 | `predicate_sig` | `varchar(1024)` | — | NN | — |
| 5 | `groupby_sig` | `varchar(512)` | — |  | — |
| 6 | `first_seen` | `timestamptz` | — | NN | — |
| 7 | `times_seen` | `integer` | — | NN | — |
| 8 | `table_id` | `integer` | FK→`sys_table.table_id` | NN | — |
| 9 | `target_column_id` | `integer` | FK→`sys_column.column_id` |  | — |

**Check constraints**

- `ck_fingerprint_times_seen_pos` — `((times_seen >= 1))`

**Indexes** (5 total, 3 secondary)

- `idx_fp_table_agg` — btree (table_id, agg_kind)
- `sys_query_fingerprint_table_id_ec47e8e5` — btree (table_id)
- `sys_query_fingerprint_target_column_id_c14c9283` — btree (target_column_id)

### `sys_role`  — 4 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `role_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `role_name` | `varchar(64)` | U | NN | — |
| 3 | `description` | `varchar(255)` | — |  | — |

**Indexes** (3 total, 1 secondary)

- `sys_role_role_name_d13c3c79_like` — btree (role_name varchar_pattern_ops)

### `sys_table`  — 2 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `table_id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `table_name` | `varchar(64)` | U | NN | — |
| 3 | `root_page_id` | `integer` | — | NN | — |
| 4 | `n_pages` | `integer` | — | NN | — |
| 5 | `est_row_count` | `bigint` | — | NN | — |
| 6 | `is_system` | `boolean` | — | NN | — |
| 7 | `created_at` | `timestamptz` | — | NN | — |
| 8 | `db_id` | `integer` | FK→`sys_database.db_id` U | NN | — |
| 9 | `privacy_unit_col` | `integer` | FK→`sys_column.column_id` |  | — |

**Indexes** (4 total, 3 secondary)

- `sys_table_db_id_f4d3fca7` — btree (db_id)
- `sys_table_privacy_unit_col_06991647` — btree (privacy_unit_col)
- `uq_table_db_name` — UNIQUE btree (db_id, table_name)

### `sys_table_privacy`  — 1 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `table_id` | `integer` | PK FK→`sys_table.table_id` | NN | — |
| 2 | `max_rows_per_unit` | `integer` | — | NN | — |
| 3 | `max_groups_per_unit` | `integer` | — | NN | — |
| 4 | `enforce_dp` | `boolean` | — | NN | — |
| 5 | `min_partition_rho` | `float8` | — | NN | — |
| 6 | `privacy_unit_col` | `integer` | FK→`sys_column.column_id` | NN | — |

**Check constraints**

- `ck_tabpriv_max_groups_pos` — `((max_groups_per_unit >= 1))`
- `ck_tabpriv_max_rows_pos` — `((max_rows_per_unit >= 1))`
- `ck_tabpriv_min_rho_pos` — `((min_partition_rho > (0.0)::double precision))`

**Indexes** (2 total, 1 secondary)

- `sys_table_privacy_privacy_unit_col_1c956a08` — btree (privacy_unit_col)

## 3. Framework and auth internals

Created by Django and SimpleJWT. Listed for completeness; not part of the ER model.

### `auth_group`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `name` | `varchar(150)` | U | NN | — |

**Indexes** (3 total, 1 secondary)

- `auth_group_name_a6ea08ec_like` — btree (name varchar_pattern_ops)

### `auth_group_permissions`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `group_id` | `integer` | FK→`auth_group.id` U | NN | — |
| 3 | `permission_id` | `integer` | FK→`auth_permission.id` U | NN | — |

**Indexes** (4 total, 3 secondary)

- `auth_group_permissions_group_id_b120cbf9` — btree (group_id)
- `auth_group_permissions_group_id_permission_id_0cd325b0_uniq` — UNIQUE btree (group_id, permission_id)
- `auth_group_permissions_permission_id_84c5c92e` — btree (permission_id)

### `auth_permission`  — 140 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `name` | `varchar(255)` | — | NN | — |
| 3 | `content_type_id` | `integer` | FK→`django_content_type.id` U | NN | — |
| 4 | `codename` | `varchar(100)` | U | NN | — |

**Indexes** (3 total, 2 secondary)

- `auth_permission_content_type_id_2f476e4b` — btree (content_type_id)
- `auth_permission_content_type_id_codename_01ab375a_uniq` — UNIQUE btree (content_type_id, codename)

### `django_admin_log`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `integer` | PK | NN | `identity (by default)` |
| 2 | `action_time` | `timestamptz` | — | NN | — |
| 3 | `object_id` | `text` | — |  | — |
| 4 | `object_repr` | `varchar(200)` | — | NN | — |
| 5 | `action_flag` | `smallint` | — | NN | — |
| 6 | `change_message` | `text` | — | NN | — |
| 7 | `content_type_id` | `integer` | FK→`django_content_type.id` |  | — |
| 8 | `user_id` | `bigint` | FK→`sys_principal.id` | NN | — |

**Check constraints**

- `django_admin_log_action_flag_check` — `((action_flag >= 0))`

**Indexes** (3 total, 2 secondary)

- `django_admin_log_content_type_id_c4bce8eb` — btree (content_type_id)
- `django_admin_log_user_id_c564eba6` — btree (user_id)

### `django_content_type`  — 35 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `integer` | PK | NN | `identity (by default)` |
| 3 | `app_label` | `varchar(100)` | U | NN | — |
| 4 | `model` | `varchar(100)` | U | NN | — |

**Indexes** (2 total, 1 secondary)

- `django_content_type_app_label_model_76bd3d3b_uniq` — UNIQUE btree (app_label, model)

### `django_migrations`  — 47 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `app` | `varchar(255)` | — | NN | — |
| 3 | `name` | `varchar(255)` | — | NN | — |
| 4 | `applied` | `timestamptz` | — | NN | — |

### `django_session`

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `session_key` | `varchar(40)` | PK | NN | — |
| 2 | `session_data` | `text` | — | NN | — |
| 3 | `expire_date` | `timestamptz` | — | NN | — |

**Indexes** (3 total, 1 secondary)

- `django_session_expire_date_a5c62663` — btree (expire_date)

### `token_blacklist_blacklistedtoken`  — 21 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 2 | `blacklisted_at` | `timestamptz` | — | NN | — |
| 3 | `token_id` | `bigint` | FK→`token_blacklist_outstandingtoken.id` U | NN | — |

### `token_blacklist_outstandingtoken`  — 29 rows

| # | Column | Type | Key | Null | Default |
|---|---|---|---|---|---|
| 1 | `id` | `bigint` | PK | NN | `identity (by default)` |
| 3 | `token` | `text` | — | NN | — |
| 4 | `created_at` | `timestamptz` | — |  | — |
| 5 | `expires_at` | `timestamptz` | — | NN | — |
| 6 | `user_id` | `bigint` | FK→`sys_principal.id` |  | — |
| 7 | `jti` | `varchar(255)` | U | NN | — |

**Indexes** (4 total, 3 secondary)

- `token_blacklist_outstandingtoken_jti_hex_d9bdf6f7_like` — btree (jti varchar_pattern_ops)
- `token_blacklist_outstandingtoken_jti_hex_d9bdf6f7_uniq` — UNIQUE btree (jti)
- `token_blacklist_outstandingtoken_user_id_83bc629a` — btree (user_id)

