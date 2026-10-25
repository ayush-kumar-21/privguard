# PrivGuard — Entity-Relationship Design Documentation

**Project:** PrivGuard — A Differentially Private Query Execution Engine with Budget-Aware Storage Semantics
**Deliverable:** ER model — entity identification, attribute enumeration, relationship establishment with cardinalities

---

Rendered diagrams: [`docs/er/er-catalog-privacy.png`](docs/er/er-catalog-privacy.png) and [`docs/er/er-safehealth.png`](docs/er/er-safehealth.png) (Mermaid sources alongside).

## Figure Index

| Figure | File | Notation | Scope |
|---|---|---|---|
| 1 | `fig1_catalog_crowsfoot.png` | Crow's Foot | System catalog + privacy subsystem (20 entities, all attributes) |
| 2 | `fig2_safehealth_crowsfoot.png` | Crow's Foot | SafeHealth application dataset (8 entities, all attributes) |
| 3 | `fig3_safehealth_chen.png` | **Chen** | SafeHealth dataset — entities, attribute ellipses, relationship diamonds |
| 4 | `fig4_privacy_chen.png` | **Chen** | Privacy budget & query audit subsystem |
| 5 | `fig5_overview.png` | Crow's Foot (compact) | Consolidated view — the original 28 entities and 39 relationships (the two patient-budget entities R40–R42 are in `docs/er/er-catalog-privacy.mmd`) |

Each figure is supplied as **PNG** (for pasting into a report) and **SVG** (vector — scales without blurring, use this in Word/LaTeX if possible).

> **Added after the figures were generated:** `SYS_PATIENT_BUDGET`, `SYS_PATIENT_LEDGER` and R40–R42 are in `docs/er/er-catalog-privacy.mmd`. Regenerate the picture with `npx @mermaid-js/mermaid-cli -i docs/er/er-catalog-privacy.mmd -o docs/er/er-catalog-privacy.png` before submitting.

> **Note on tooling.** These were laid out programmatically with Graphviz for consistency and precision. If your submission requires a named tool, see §5 for how to import this design into Lucidchart, draw.io, or StarUML in under five minutes.

---

## 1. Entity Inventory

**Total entities: 28**, grouped into six subsystems.

### Subsystem A — Structural Catalog (5 entities)

| # | Entity | Type | Primary Key | Purpose |
|---|---|---|---|---|
| 1 | `SYS_DATABASE` | Strong | `db_id` | One row per logical database; holds default privacy parameters |
| 2 | `SYS_TABLE` | Strong | `table_id` | Table metadata and heap-file root page pointer |
| 3 | `SYS_COLUMN` | Strong | `column_id` | Column metadata, type, encryption flag |
| 4 | `SYS_INDEX` | Strong | `index_id` | B+ tree index metadata |
| 5 | `SYS_INDEX_COLUMN` | **Associative** | (`index_id`, `ordinal`) | Resolves the M:N between indexes and columns |

### Subsystem B — Privacy Policy (2 entities)

| # | Entity | Type | Primary Key | Purpose |
|---|---|---|---|---|
| 6 | `SYS_TABLE_PRIVACY` | Strong (1:1 extension) | `table_id` | Declares the privacy unit and contribution bounds per table |
| 7 | `SYS_COLUMN_PRIVACY` | Strong (1:1 extension) | `policy_id` | Mechanism, clamp bounds, and partition threshold per column |

### Subsystem C — Identity and Access Control (7 entities)

| # | Entity | Type | Primary Key | Purpose |
|---|---|---|---|---|
| 8 | `SYS_PRINCIPAL` | Strong | `principal_id` | Analysts and service accounts |
| 9 | `SYS_ROLE` | Strong | `role_id` | Named roles (`dp_analyst`, `clinician`, `auditor`, `dba`) |
| 10 | `SYS_PRINCIPAL_ROLE` | **Associative** | (`principal_id`, `role_id`) | Resolves M:N between principals and roles |
| 11 | `SYS_ATTRIBUTE` | Strong | `attr_id` | Attribute universe for attribute-based encryption |
| 12 | `SYS_PRINCIPAL_ATTRIBUTE` | **Associative** | (`principal_id`, `attr_id`) | Resolves M:N between principals and attributes |
| 13 | `SYS_COLUMN_GRANT` | **Associative** | `grant_id` | Column × role access mode; resolves M:N with payload |
| 14 | `SYS_KEY_MATERIAL` | Strong | `key_id` | Wrapped data-encryption keys per encrypted column |

### Subsystem D — Privacy Budget Ledger (4 entities)

| # | Entity | Type | Primary Key | Purpose |
|---|---|---|---|---|
| 15 | `SYS_BUDGET_ACCOUNT` | Strong | `account_id` | Per-principal, per-database ε/ρ allocation and running spend |
| 16 | `SYS_BUDGET_LEDGER` | **Weak** (existence-dependent on account) | `entry_id` | Append-only, hash-chained record of every RESERVE / COMMIT / ABORT |
| 29 | `SYS_PATIENT_BUDGET` | Strong (1:1 extension of `PATIENTS`, the privacy unit) | `patient_id` | Per-patient ρ allowance shared by all analysts; makes extra analyst accounts worthless for collusion |
| 30 | `SYS_PATIENT_LEDGER` | **Weak** (existence-dependent on the patient) | `entry_id` | Append-only, per-patient hash-chained record of every CHARGE / REFUND |

### Subsystem E — Query Audit and Rewriter State (4 entities)

| # | Entity | Type | Primary Key | Purpose |
|---|---|---|---|---|
| 17 | `SYS_QUERY` | Strong | `query_id` | One row per submitted query, admitted or rejected |
| 18 | `SYS_QUERY_FINGERPRINT` | Strong | `fingerprint_id` | Canonicalised AST hash used for deduplication |
| 19 | `SYS_NOISE_CACHE` | **Weak** (dependent on fingerprint) | `cache_id` | Memoized noisy answer — defeats the averaging attack |
| 20 | `SYS_PREDICATE_OVERLAP` | **Associative** (recursive) | `overlap_id` | Pairwise fingerprint comparison for differencing-attack detection |

### Subsystem F — SafeHealth Application Data (8 entities)

| # | Entity | Type | Primary Key | Purpose |
|---|---|---|---|---|
| 21 | `REGIONS` | Strong | `region_id` | Geographic dimension |
| 22 | `HOSPITALS` | Strong | `hospital_id` | Hospital dimension |
| 23 | `DEPARTMENTS` | Strong | `dept_id` | Department dimension |
| 24 | `STAFF` | Strong | `staff_id` | Clinical staff |
| 25 | `PATIENTS` | **Strong — PRIVACY UNIT** | `patient_id` | Neighbouring databases differ by exactly one row here |
| 26 | `VISITS` | Strong (fact) | `visit_id` | Central fact table; carries the aggregated measures |
| 27 | `DIAGNOSES` | Strong | `diagnosis_id` | Visit-level diagnosis lines |
| 28 | `LAB_RESULTS` | Strong | `lab_id` | Visit-level laboratory measurements |

---

## 2. Attribute Enumeration

Legend: **PK** primary key · **FK** foreign key · **UK** unique key · **PU** privacy unit · *derived* · 🔒 encrypted at rest

### 2.1 Structural Catalog

| Entity | Attributes |
|---|---|
| `SYS_DATABASE` | **PK** `db_id` INT · **UK** `db_name` VARCHAR(64) · `dp_model` VARCHAR(16) · `default_rho` DOUBLE · `default_delta` DOUBLE · `created_at` TIMESTAMP |
| `SYS_TABLE` | **PK** `table_id` INT · **FK** `db_id` · `table_name` VARCHAR(64) · `root_page_id` INT · `n_pages` INT · `est_row_count` BIGINT · `is_system` BOOLEAN · **FK** `privacy_unit_col` · `created_at` TIMESTAMP |
| `SYS_COLUMN` | **PK** `column_id` INT · **FK** `table_id` · `col_name` VARCHAR(64) · `ordinal` INT · `data_type` VARCHAR(24) · `type_len` INT · `is_nullable` BOOLEAN · `is_privacy_unit` BOOLEAN · `is_encrypted` BOOLEAN |
| `SYS_INDEX` | **PK** `index_id` INT · **FK** `table_id` · **UK** `index_name` VARCHAR(64) · `root_page_id` INT · `index_type` VARCHAR(16) · `is_unique` BOOLEAN · `height` INT |
| `SYS_INDEX_COLUMN` | **PK,FK** `index_id` · **PK** `ordinal` INT · **FK** `column_id` |

### 2.2 Privacy Policy

| Entity | Attributes |
|---|---|
| `SYS_TABLE_PRIVACY` | **PK,FK** `table_id` · **FK** `privacy_unit_col` · `max_rows_per_unit` INT · `max_groups_per_unit` INT · `enforce_dp` BOOLEAN · `min_partition_rho` DOUBLE |
| `SYS_COLUMN_PRIVACY` | **PK** `policy_id` INT · **UK,FK** `column_id` · `mechanism` VARCHAR(20) · `lower_clamp` DOUBLE · `upper_clamp` DOUBLE · `max_contrib_per_unit` INT · `is_groupable` BOOLEAN · `is_filterable` BOOLEAN · `min_partition_size` INT |

### 2.3 Identity and Access Control

| Entity | Attributes |
|---|---|
| `SYS_PRINCIPAL` | **PK** `principal_id` INT · **UK** `principal_name` VARCHAR(64) · `kind` VARCHAR(12) · `pwd_hash` VARCHAR(255) · `is_active` BOOLEAN · `created_at` TIMESTAMP |
| `SYS_ROLE` | **PK** `role_id` INT · **UK** `role_name` VARCHAR(64) · `description` VARCHAR(255) |
| `SYS_PRINCIPAL_ROLE` | **PK,FK** `principal_id` · **PK,FK** `role_id` · `granted_at` TIMESTAMP |
| `SYS_ATTRIBUTE` | **PK** `attr_id` INT · **UK** `attr_name` VARCHAR(64) |
| `SYS_PRINCIPAL_ATTRIBUTE` | **PK,FK** `principal_id` · **PK,FK** `attr_id` · `granted_at` TIMESTAMP · `expires_at` TIMESTAMP |
| `SYS_COLUMN_GRANT` | **PK** `grant_id` INT · **FK** `column_id` · **FK** `role_id` · `access_mode` VARCHAR(12) · `rho_multiplier` DOUBLE · `abe_policy_expr` VARCHAR(255) |
| `SYS_KEY_MATERIAL` | **PK** `key_id` INT · **FK** `column_id` · `algo` VARCHAR(24) · `wrapped_dek` VARBINARY(512) · `abe_policy_expr` VARCHAR(255) · `key_version` INT · `created_at` TIMESTAMP |

### 2.4 Privacy Budget Ledger

| Entity | Attributes |
|---|---|
| `SYS_BUDGET_ACCOUNT` | **PK** `account_id` INT · **FK** `principal_id` · **FK** `db_id` · `rho_total` DOUBLE · `rho_spent` DOUBLE · `rho_reserved` DOUBLE · `delta_target` DOUBLE · `window_type` VARCHAR(12) · `window_start` TIMESTAMP · `refill_period_s` BIGINT · `status` VARCHAR(12) |
| `SYS_PATIENT_BUDGET` | **PK/FK** `patient_id` INT · `rho_total` DOUBLE · `rho_spent` DOUBLE · `status` VARCHAR(12) |
| `SYS_PATIENT_LEDGER` | **PK** `entry_id` BIGINT · **FK** `patient_id` · **FK** `query_id` · `op` VARCHAR(10) · `rho_delta` DOUBLE · `rho_after` DOUBLE · `logged_at` TIMESTAMP · `prev_hash` BINARY(32) · `entry_hash` BINARY(32) |
| `SYS_BUDGET_LEDGER` | **PK** `entry_id` BIGINT · **FK** `account_id` · **FK** `query_id` · `op` VARCHAR(10) · `rho_delta` DOUBLE · `rho_after` DOUBLE · `eps_equiv` DOUBLE · `mechanism` VARCHAR(20) · `sensitivity` DOUBLE · `sigma_or_scale` DOUBLE · `logged_at` TIMESTAMP · `prev_hash` BINARY(32) · `entry_hash` BINARY(32) |

### 2.5 Query Audit and Rewriter State

| Entity | Attributes |
|---|---|
| `SYS_QUERY` | **PK** `query_id` BIGINT · **FK** `principal_id` · **FK** `db_id` · **FK** `fingerprint_id` · `sql_text` VARCHAR(4000) · `plan_json` VARCHAR(8000) · `status` VARCHAR(24) · `rho_charged` DOUBLE · `rows_scanned` BIGINT · `latency_ms` DOUBLE · `submitted_at` TIMESTAMP |
| `SYS_QUERY_FINGERPRINT` | **PK** `fingerprint_id` BIGINT · **UK** `ast_hash` BINARY(32) · **FK** `table_id` · **FK** `target_column_id` · `agg_kind` VARCHAR(12) · `predicate_sig` VARCHAR(1024) · `groupby_sig` VARCHAR(512) · `first_seen` TIMESTAMP · `times_seen` INT |
| `SYS_NOISE_CACHE` | **PK** `cache_id` BIGINT · **FK** `fingerprint_id` · **FK** `principal_id` · `noise_seed` BINARY(32) · `cached_result` VARCHAR(4000) · `rho_charged` DOUBLE · `created_at` TIMESTAMP |
| `SYS_PREDICATE_OVERLAP` | **PK** `overlap_id` BIGINT · **FK** `fp_a` · **FK** `fp_b` · **FK** `principal_id` · `jaccard_est` DOUBLE · `symmetric_diff_est` BIGINT · `action_taken` VARCHAR(20) · `detected_at` TIMESTAMP |

### 2.6 SafeHealth Application Data

| Entity | Attributes |
|---|---|
| `REGIONS` | **PK** `region_id` INT · `region_name` VARCHAR(64) · `state_code` VARCHAR(4) |
| `HOSPITALS` | **PK** `hospital_id` INT · **FK** `region_id` · `hospital_name` VARCHAR(128) · `bed_count` INT |
| `DEPARTMENTS` | **PK** `dept_id` INT · **FK** `hospital_id` · `dept_name` VARCHAR(64) · `specialty` VARCHAR(64) |
| `STAFF` | **PK** `staff_id` INT · **FK** `dept_id` · `full_name` VARCHAR(128) · `staff_role` VARCHAR(32) · `hire_date` DATE |
| `PATIENTS` | **PU,PK** `patient_id` INT · **FK** `region_id` · 🔒 `mrn` VARCHAR(32) · `birth_year` INT · *`age`* INT (derived) · `sex` VARCHAR(8) · `zip3` VARCHAR(3) · `insurance_type` VARCHAR(24) · `enrolled_at` DATE |
| `VISITS` | **PK** `visit_id` BIGINT · **FK** `patient_id` · **FK** `dept_id` · **FK** `attending_staff_id` · `admit_ts` TIMESTAMP · `discharge_ts` TIMESTAMP · *`length_of_stay_days`* INT (derived) · `total_charge` DOUBLE · `admission_type` VARCHAR(16) |
| `DIAGNOSES` | **PK** `diagnosis_id` BIGINT · **FK** `visit_id` · `icd10_code` VARCHAR(10) · `is_primary` BOOLEAN · `severity` INT |
| `LAB_RESULTS` | **PK** `lab_id` BIGINT · **FK** `visit_id` · `test_code` VARCHAR(16) · `value_num` DOUBLE · `unit` VARCHAR(16) · `taken_at` TIMESTAMP |

---

## 3. Relationship Set with Cardinalities

**Total relationships: 42** (R1–R39 as originally designed; R40–R42 added with the per-patient budget). Participation: *Total* = mandatory (every instance must participate), *Partial* = optional.

| # | Parent Entity | Relationship | Child Entity | Cardinality | Parent Part. | Child Part. |
|---|---|---|---|---|---|---|
| R1 | `SYS_DATABASE` | contains | `SYS_TABLE` | 1 : N | Partial | Total |
| R2 | `SYS_TABLE` | has | `SYS_COLUMN` | 1 : N | Total | Total |
| R3 | `SYS_TABLE` | is indexed by | `SYS_INDEX` | 1 : N | Partial | Total |
| R4 | `SYS_INDEX` | spans | `SYS_INDEX_COLUMN` | 1 : N | Total | Total |
| R5 | `SYS_COLUMN` | participates in | `SYS_INDEX_COLUMN` | 1 : N | Partial | Total |
| R6 | `SYS_TABLE` | is governed by | `SYS_TABLE_PRIVACY` | 1 : 1 | Partial | Total |
| R7 | `SYS_COLUMN` | is policed by | `SYS_COLUMN_PRIVACY` | 1 : 1 | Partial | Total |
| R8 | `SYS_COLUMN` | is privacy unit of | `SYS_TABLE_PRIVACY` | 1 : 1 | Partial | Total |
| R9 | `SYS_PRINCIPAL` | assumes | `SYS_PRINCIPAL_ROLE` | 1 : N | Total | Total |
| R10 | `SYS_ROLE` | is assigned to | `SYS_PRINCIPAL_ROLE` | 1 : N | Partial | Total |
| R11 | `SYS_PRINCIPAL` | holds | `SYS_PRINCIPAL_ATTRIBUTE` | 1 : N | Partial | Total |
| R12 | `SYS_ATTRIBUTE` | is held by | `SYS_PRINCIPAL_ATTRIBUTE` | 1 : N | Partial | Total |
| R13 | `SYS_COLUMN` | is exposed via | `SYS_COLUMN_GRANT` | 1 : N | Partial | Total |
| R14 | `SYS_ROLE` | is granted | `SYS_COLUMN_GRANT` | 1 : N | Partial | Total |
| R15 | `SYS_COLUMN` | is encrypted by | `SYS_KEY_MATERIAL` | 1 : N | Partial | Total |
| R16 | `SYS_PRINCIPAL` | owns | `SYS_BUDGET_ACCOUNT` | 1 : N | Total | Total |
| R17 | `SYS_DATABASE` | scopes | `SYS_BUDGET_ACCOUNT` | 1 : N | Partial | Total |
| R18 | `SYS_BUDGET_ACCOUNT` | appends *(identifying)* | `SYS_BUDGET_LEDGER` | 1 : N | Total | Total |
| R19 | `SYS_QUERY` | justifies | `SYS_BUDGET_LEDGER` | 1 : N | Partial | Total |
| R20 | `SYS_PRINCIPAL` | submits | `SYS_QUERY` | 1 : N | Partial | Total |
| R21 | `SYS_DATABASE` | receives | `SYS_QUERY` | 1 : N | Partial | Total |
| R22 | `SYS_QUERY_FINGERPRINT` | canonicalises | `SYS_QUERY` | 1 : N | Partial | Partial |
| R23 | `SYS_TABLE` | is targeted by | `SYS_QUERY_FINGERPRINT` | 1 : N | Partial | Total |
| R24 | `SYS_COLUMN` | is aggregated in | `SYS_QUERY_FINGERPRINT` | 1 : N | Partial | Partial |
| R25 | `SYS_QUERY_FINGERPRINT` | memoizes *(identifying)* | `SYS_NOISE_CACHE` | 1 : N | Partial | Total |
| R26 | `SYS_PRINCIPAL` | keys | `SYS_NOISE_CACHE` | 1 : N | Partial | Total |
| R27 | `SYS_QUERY_FINGERPRINT` | is compared as A | `SYS_PREDICATE_OVERLAP` | 1 : N | Partial | Total |
| R28 | `SYS_QUERY_FINGERPRINT` | is compared as B | `SYS_PREDICATE_OVERLAP` | 1 : N | Partial | Total |
| R29 | `SYS_PRINCIPAL` | is observed for | `SYS_PREDICATE_OVERLAP` | 1 : N | Partial | Total |
| R30 | `SYS_TABLE` | describes *(metadata)* | *application tables* | 1 : N | Partial | Total |
| R31 | `REGIONS` | is located in | `HOSPITALS` | 1 : N | Partial | Total |
| R32 | `REGIONS` | is residence of | `PATIENTS` | 1 : N | Partial | Total |
| R33 | `HOSPITALS` | operates | `DEPARTMENTS` | 1 : N | Total | Total |
| R34 | `DEPARTMENTS` | employs | `STAFF` | 1 : N | Total | Total |
| R35 | `PATIENTS` | makes | `VISITS` | 1 : N | Partial | Total |
| R36 | `DEPARTMENTS` | hosts | `VISITS` | 1 : N | Partial | Total |
| R37 | `STAFF` | attends | `VISITS` | 1 : N | Partial | Total |
| R38 | `VISITS` | yields | `DIAGNOSES` | 1 : N | Total | Total |
| R39 | `VISITS` | produces | `LAB_RESULTS` | 1 : N | Partial | Total |
| R40 | `PATIENTS` | has allowance | `SYS_PATIENT_BUDGET` | 1 : 1 | Partial | Total |
| R41 | `PATIENTS` | appends *(identifying)* | `SYS_PATIENT_LEDGER` | 1 : N | Partial | Total |
| R42 | `SYS_QUERY` | charges | `SYS_PATIENT_LEDGER` | 1 : N | Partial | Total |

### 3.1 Many-to-Many Relationships and Their Resolution

Four M:N relationships exist conceptually and are resolved into associative entities in the logical design:

| Conceptual M:N | Resolved via | Composite Key |
|---|---|---|
| `SYS_INDEX` **M:N** `SYS_COLUMN` | `SYS_INDEX_COLUMN` | (`index_id`, `ordinal`) |
| `SYS_PRINCIPAL` **M:N** `SYS_ROLE` | `SYS_PRINCIPAL_ROLE` | (`principal_id`, `role_id`) |
| `SYS_PRINCIPAL` **M:N** `SYS_ATTRIBUTE` | `SYS_PRINCIPAL_ATTRIBUTE` | (`principal_id`, `attr_id`) |
| `SYS_COLUMN` **M:N** `SYS_ROLE` | `SYS_COLUMN_GRANT` | (`column_id`, `role_id`) UK |

### 3.2 Recursive Relationship

`SYS_QUERY_FINGERPRINT` participates in a **unary (recursive) M:N relationship with itself** through `SYS_PREDICATE_OVERLAP`, which stores pairwise comparisons (`fp_a`, `fp_b`) used to detect differencing attacks. This is the only recursive relationship in the design and is worth pointing out explicitly in your viva.

---

## 4. Design Notes Worth Defending

### Why `STAFF` is not a users table

The course brief forbids separate login tables such as Admin / Student / Faculty / Employee. `STAFF`
(Subsystem F) is **not** one: it holds hospital HR facts — `full_name`, `staff_role` (job title),
`hire_date`, department — for the *attending physician* foreign key on `VISITS`. It has no
credentials and no login. Every account that can sign in is a row in `SYS_PRINCIPAL`
(`accounts.Principal`, the single users table); its permissions come from `SYS_ROLE` via
`SYS_PRINCIPAL_ROLE`.

**Why is `SYS_BUDGET_LEDGER` modelled as a weak entity?**
A ledger entry has no meaning outside its owning budget account — it records a state transition *of that account*. Its existence is fully dependent on the account, and cascade deletion would be semantically correct (though in practice the ledger is never deleted, being append-only and hash-chained).

**Why `1:1` between `SYS_TABLE` and `SYS_TABLE_PRIVACY` rather than merging them?**
Vertical partitioning. Not every table is privacy-governed (public dimension tables such as `REGIONS` are not), so merging would produce a wide table with many NULLs. Keeping it separate also means the privacy policy can be versioned and audited independently of table structure.

**Why does `PATIENTS` get special treatment?**
It is the **privacy unit**. The formal DP guarantee is defined over *neighbouring databases* that differ by one row of `PATIENTS`, not one row of `VISITS`. Every DP query must therefore trace a functional path from the queried fact table back to `patient_id` — which is exactly why `VISITS.patient_id` is non-nullable and why `DIAGNOSES` and `LAB_RESULTS` reach the privacy unit transitively through `VISITS`.

**Why is `age` derived rather than stored?**
Storing both `birth_year` and `age` would create an update anomaly (age changes annually). It is shown as a derived attribute (dashed ellipse in Figure 3) computed as `CURRENT_YEAR − birth_year`.

**Normalisation.** All 30 entities are in **3NF**. There are no partial dependencies (every non-key attribute depends on the whole key, including in the four composite-key associative entities) and no transitive dependencies. The one deliberate denormalisation is `SYS_BUDGET_ACCOUNT.rho_spent`, which is a materialised aggregate over `SYS_BUDGET_LEDGER`. It is justified because the budget check sits on the hot path of every query, and its correctness is enforced by the ledger invariant:

```
rho_spent  ==  Σ rho_delta WHERE op = 'COMMIT'  −  Σ rho_delta WHERE op = 'REFILL'
```

verified by the `VERIFY LEDGER` command. Mention this trade-off explicitly — examiners like seeing a denormalisation that is *identified, justified, and guarded by an invariant* rather than accidental.

---

## 5. Importing This Design into Lucidchart / draw.io / StarUML

If your submission requires one of the named tools, the fastest route is a **DDL import**, which auto-generates the ER diagram from the schema:

**Lucidchart**
1. New document → **Import Data → Import SQL**
2. Paste the `CREATE TABLE` statements from §3 of the main project plan (or the `privguard_schema.sql` file)
3. Lucidchart infers entities, attributes, PKs, and FK relationships automatically
4. Arrange the generated shapes, then **File → Download → PNG** for your screenshot

**draw.io / diagrams.net**
1. **Arrange → Insert → Advanced → Mermaid**
2. Paste the `erDiagram` Mermaid blocks from §4 of the main project plan
3. Or: **Extras → Edit Diagram** and paste, then export as PNG at 300 DPI

**StarUML**
1. Create a **Data Model** (not a Class Model)
2. Use **Tools → Extension Manager** to install the *DDL Import* extension
3. Import the schema file; entities and relationships are generated as an ERD

**Erwin / MySQL Workbench** (also commonly accepted)
- MySQL Workbench: **Database → Reverse Engineer** on a database created from the DDL produces a clean EER diagram in one step.

Whichever tool you use, keep the cardinality table in §3 alongside the picture — a diagram plus an explicit relationship table is what earns full marks, because the table makes participation constraints unambiguous where the drawing alone can be read two ways.
