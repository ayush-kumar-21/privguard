# PrivGuard Engine — Frozen Specification

Version 1.0 · frozen 2026-09-03 (Phase 1.3)

This document is the contract the Rust engine is built against. The Python
oracle in `oracle/` implements the same semantics independently; any divergence
between the two is a bug in one of them, and the differential test in Phase 3.8
is what catches it.

Changes to this document after the freeze must be recorded in the Amendments
section at the bottom, never edited silently in place.

---

## 1. Scope

**In scope.** A single-node, single-user-at-a-time query engine that stores the
SafeHealth dataset in its own page-based files and answers aggregate SQL under
differential privacy, charging a per-principal budget recorded in an append-only
hash-chained ledger.

**Explicitly not in scope** (from the blueprint):

- transactions / MVCC — one writer, no concurrent DML during query serving
- cross-unit joins — every query aggregates over the one privacy-unit fact table
- crash recovery — stretch goal only; a torn write may lose the last page

**Consequences of the non-goals.** Because there is no MVCC, the budget ledger
gets its atomicity from a single-threaded reserve/commit path plus the hash
chain, not from a transaction manager. Because there are no cross-unit joins,
sensitivity analysis never has to reason about join amplification.

---

## 2. Data model

### 2.1 Privacy unit

The privacy unit is **`patients.patient_id`**. Every privacy guarantee is stated
per patient: adding or removing one patient's entire set of rows changes any
released answer by a bounded amount.

### 2.2 SafeHealth schema

`visits` is the privacy-unit fact table — the only table an analyst may
aggregate over in v1.

| Table | Columns | Role |
|---|---|---|
| `regions` | `region_id` PK, `region_name`, `state_code` | dimension |
| `hospitals` | `hospital_id` PK, `region_id` FK, `hospital_name`, `bed_count` | dimension |
| `departments` | `dept_id` PK, `hospital_id` FK, `dept_name`, `specialty` | dimension |
| `staff` | `staff_id` PK, `dept_id` FK, `full_name`, `staff_role`, `hire_date` | dimension |
| `patients` | `patient_id` PK, `region_id` FK, `mrn`, `birth_year`, `sex`, `insurance_type`, `enrolled_at` | **privacy unit** |
| `visits` | `visit_id` PK, `patient_id` FK, `dept_id` FK, `attending_staff_id` FK, `admit_ts`, `discharge_ts`, `total_charge`, `admission_type` | **fact** |
| `diagnoses` | `diagnosis_id` PK, `visit_id` FK, `icd10_code`, `is_primary`, `severity` | fact detail |
| `lab_results` | `lab_id` PK, `visit_id` FK, `test_code`, `value_num`, `unit`, `taken_at` | fact detail |

### 2.3 Column types

The engine supports exactly these physical types in v1:

| Type | Encoding | Width |
|---|---|---|
| `I32` | little-endian two's complement | 4 B |
| `I64` | little-endian two's complement | 8 B |
| `F64` | IEEE-754 little-endian | 8 B |
| `Text` | 2-byte length prefix + UTF-8 | variable |
| `Timestamp` | microseconds since Unix epoch, `I64` | 8 B |
| `Bool` | one byte, 0 or 1 | 1 B |

`NULL` is tracked in a per-tuple null bitmap, not by a sentinel value.

---

## 3. SQL subset

The analyst-facing grammar in v1 — deliberately small, because every construct
admitted has to have a proven sensitivity bound.

```
SELECT <agg>(<col> | *) FROM visits
  [ WHERE <predicate> [ AND <predicate> ]* ]
  [ GROUP BY <col> ]

agg       : COUNT | SUM | AVG
predicate : <col> <op> <literal>
op        : = | != | <> | > | >= | < | <=
```

**Restrictions.**

- `COUNT` takes `*` only. `COUNT(col)` is rejected — its sensitivity differs
  from `COUNT(*)` under null handling and the distinction invites mistakes.
- `SUM` and `AVG` require a numeric column that carries a column privacy policy
  with both clamp bounds set. Without clamps there is no finite sensitivity.
- Only `AND` composition in `WHERE`. `OR` and `NOT` are rejected in v1: they
  make predicate-overlap detection materially harder.
- At most one `GROUP BY` column, and it must be marked groupable in the catalog.
- `AVG` with `GROUP BY` is rejected in v1 (ratio-of-noisy-values per group has
  poor utility and no clean zCDP accounting).

**Filterable columns** (`visits`): `total_charge`, `admission_type`, `dept_id`,
`admit_ts`, `discharge_ts`.
**Groupable columns** (`visits`): `admission_type`, `dept_id`.

Analyst text is parsed to an AST and *never* re-emitted as SQL against a
downstream database. The engine executes the plan itself.

---

## 4. Privacy semantics

### 4.1 Accounting — zCDP

Composition is additive in ρ, which is exactly why the ledger is a running sum.

```
Gaussian   ρ = Δ₂² / (2σ²)          ⇒  σ  = Δ₂ / √(2ρ)
Laplace    ε₀-DP is (ε₀²/2)-zCDP    ⇒  ε₀ = √(2ρ),  b = Δ₁ / ε₀
Reporting  ε(δ) = ρ + 2√(ρ · ln(1/δ))
```

Budgets are held and spent in ρ. (ε, δ) is a **reporting projection only** —
never the accounting unit.

### 4.2 Contribution bounding

Two bounds, both applied before aggregation, both from the table privacy policy:

- **C** = `max_rows_per_unit` — the L∞ bound. At most C rows per patient survive.
- **G** = `max_groups_per_unit` — the L0 bound. A patient contributes to at most
  G groups.

Row selection under the bound is **deterministic**: order by the fact table's
primary key ascending and keep the first C. Determinism matters because the
noise cache promises the same answer for the same fingerprint.

Defaults when a table carries no policy: `C = 5`, `G = 3`.

### 4.3 Sensitivity

With clamp bounds `[lo, hi]` and `M = max(|lo|, |hi|)` (never zero — a policy
with `lo = hi = 0` is rejected at catalog load):

| Aggregate | Δ₁ | Δ₂ (grouped) |
|---|---|---|
| `COUNT(*)` | `C` | `√G · C` |
| `SUM(col)` | `C · M` | `√G · C · M` |
| `AVG(col)` | split, see below | n/a (rejected) |

`AVG` is computed as `noisy_sum / max(noisy_count, 1)`, splitting the budget
50/50: `ρ_sum = ρ/2`, `ρ_count = ρ/2`, with the sum charged at `Δ₁ = C·M` and
the count at `Δ₁ = C`.

### 4.4 Mechanisms

| Mechanism | Used for | Sampling |
|---|---|---|
| `discrete_laplace` | `COUNT` | Laplace via inverse CDF, rounded to integer |
| `laplace` | `SUM`, `AVG` scalar | inverse CDF |
| `gaussian` | grouped aggregates | Box–Muller |

**Noise is deterministic given a seed.** The seed is

```
seed = SHA-256( server_secret ‖ ast_hash ‖ principal_id_be64 ‖ salt )
```

and the uniform stream is `SHA-256(seed ‖ counter_be64)`, consumed 8 bytes at a
time, taking the top 53 bits as a float in [0, 1). Grouped execution derives two
independent sub-streams with salts `b"partition"` and `b"aggregate"`.

Determinism is not a convenience — it is what makes the noise cache sound. A
clock-seeded RNG would let an analyst average the noise away.

### 4.5 Partition selection (GROUP BY)

Groups are not released unconditionally; a group whose membership is too small
would itself leak. Budget splits `ρ_part = 0.2ρ`, `ρ_agg = 0.8ρ`.

```
ε_part = √(2 · ρ_part)
b_part = 1 / ε_part
τ      = 1 + b_part · ln( 1 / (2 · δ_part) )
```

A group survives iff `distinct_units + Laplace(b_part) > τ`. `δ_part = 1e-2` —
a per-partition knob chosen from the candidate-partition count, deliberately
*not* the mechanism δ (using the tiny mechanism δ suppresses every group).
Survivors are then noised with the Gaussian at `ρ_agg`.

The count of suppressed groups is returned to the analyst; the identities are
not.

### 4.6 Query fingerprinting and the noise cache

The **fingerprint** is `SHA-256` over the canonical form of the parsed spec:

```
canonical = agg | column-or-* | table | (col,op,value)* sorted | GROUP=<col-or-empty>
```

Sorting the predicates is what makes `a>1 AND b=2` and `b=2 AND a>1` one
fingerprint rather than two.

**Cache rule.** A `(fingerprint, principal)` pair that has been answered before
returns the *stored* answer, charges **ρ = 0**, and is logged with status
`cache_hit`. This is the blueprint's attack-resistant rewriter: repeating a
query cannot accumulate independent noise samples, so repeat-and-average fails.

The cache is per principal — two analysts asking the same question get
independently-seeded answers, and each pays once.

---

## 5. Storage format

### 5.1 Page

Fixed **8 KiB** slotted pages, RID-addressed. A RID is `(page_id: u32, slot: u16)`.

```
┌────────────────────────────────────────────────┐
│ PageHeader (24 B)                              │
│   page_id      u32                             │
│   page_kind    u8   (Heap | BTreeLeaf | ...)   │
│   flags        u8                              │
│   slot_count   u16                             │
│   free_start   u16   offset, grows down        │
│   free_end     u16   offset, grows up          │
│   lsn          u64   reserved, always 0 in v1  │
│   checksum     u32   CRC32C over the page      │
├────────────────────────────────────────────────┤
│ Slot directory — slot_count × Slot (4 B)       │
│   offset u16, length u16   (0,0) = dead slot   │
│ ↓ grows down                                   │
├────────────────────────────────────────────────┤
│ free space                                     │
├────────────────────────────────────────────────┤
│ ↑ tuples grow up from the end                  │
└────────────────────────────────────────────────┘
```

Deleting a tuple zeroes its slot but does not compact; compaction happens only
when a page is full and has reclaimable space.

### 5.2 Heap file

One file per table, pages numbered from 0. Page 0 is the file header (row count,
free-space map root). Sequential scan walks pages in order and yields live slots.

### 5.3 Buffer pool

Fixed-size frame array with **CLOCK** eviction. Each frame carries a pin count,
a dirty flag and a reference bit. The clock hand skips pinned frames; a frame
with its reference bit set gets the bit cleared and is skipped once. A dirty
frame is written before reuse. Evicting a pinned frame is a bug, not a
recoverable condition — the engine panics rather than corrupting a page.

### 5.4 B+ tree

Key-ordered index supporting point lookup and range scan. Internal nodes hold
separator keys and child page ids; leaves hold `(key, RID)` and a right-sibling
pointer for range scans. Split on overflow, propagating upward; merge on
underflow below half occupancy.

### 5.5 Column encryption

Columns marked encrypted in the catalog are stored as **AES-256-GCM**
ciphertext at the access-method layer: encrypt on write, decrypt on read, so the
page bytes on disk never hold plaintext. Each value gets a fresh 96-bit nonce
stored beside the ciphertext; the 128-bit tag is appended. The key comes from
the engine's keyring, never from the catalog.

---

## 6. Budget ledger

Append-only, hash-chained, one chain per budget account — the blueprint's
"budget as system state, exactly like a WAL".

```
entry := { seq u64, account_id u64, query_id u64, kind, rho f64,
           mechanism, sensitivity f64, scale f64, eps_equiv f64,
           prev_hash [32]u8, hash [32]u8 }

hash = blake3( seq ‖ account_id ‖ query_id ‖ kind ‖ rho_bits ‖ prev_hash )
```

`kind` ∈ `{ Reserve, Commit, Abort }`. The chain is verified by recomputing from
the genesis entry; any edit to a historical row breaks every subsequent hash.

**Protocol per query.**

1. `Reserve(ρ)` — fails if `ρ_spent + ρ_reserved + ρ > ρ_total`. On success
   `ρ_reserved += ρ`.
2. Execute the plan.
3. `Commit(ρ)` — `ρ_reserved -= ρ`, `ρ_spent += ρ`.
   `Abort(ρ)` — `ρ_reserved -= ρ`, nothing spent.

A query that never reaches step 3 leaves a dangling reservation; a reaper
releases reservations older than a timeout. **No data page is read before the
reservation succeeds** — that ordering is the enforcement point, and it is why
the budget check cannot be bypassed by an application-layer mistake.

---

## 7. Catalog

Self-hosted: the catalog lives in the engine's own heap files, so bootstrapping
reads pages before any catalog exists (hard-coded schema for `sys_*` tables).

| Table | Purpose |
|---|---|
| `sys_database` | database name, default δ |
| `sys_table` | table name, heap file id, row estimate |
| `sys_column` | column name, type, ordinal, nullability, encrypted flag |
| `sys_table_privacy` | `max_rows_per_unit` (C), `max_groups_per_unit` (G) |
| `sys_column_privacy` | mechanism, `lower_clamp`, `upper_clamp`, access mode |
| `sys_principal` | principal id, name, argon2 hash |
| `sys_role`, `sys_principal_role`, `sys_column_grant` | RBAC |
| `sys_budget_account`, `sys_budget_ledger` | budget state + chain |
| `sys_query`, `sys_query_fingerprint`, `sys_noise_cache` | audit + memoisation |

---

## 8. Wire protocol

PostgreSQL wire protocol **v3**, server side via `pgwire`, so the FastAPI
gateway can talk to the engine with stock `psycopg3`.

Supported in v1: startup, cleartext/MD5-free password auth against
`sys_principal`, **simple query** (`Q`), `RowDescription`, `DataRow`,
`CommandComplete`, `ErrorResponse`, `ReadyForQuery`, terminate.

Not supported in v1: extended query (`Parse`/`Bind`/`Execute`), COPY, cursors,
prepared statements, SSL negotiation (rejected, client retries in cleartext).

DP metadata (ρ charged, ε equivalent, mechanism, suppressed groups) rides back
as a `NoticeResponse` alongside the result rows, so a plain `psql` still works.

---

## 9. Error taxonomy

| Class | Meaning | Wire SQLSTATE |
|---|---|---|
| `ParseError` | outside the supported grammar | `42601` |
| `PolicyError` | missing clamp bounds, ungroupable column, denied column | `42501` |
| `BudgetExhausted` | reservation refused | `53400` |
| `CatalogError` | unknown table/column/database | `42P01` |
| `EngineError` | internal invariant violated | `XX000` |

A `PolicyError` raised *after* a successful reservation must `Abort` the
reservation before propagating. Any path that returns without settling a
reservation is a defect.

---

## 10. Amendments

**A1 — physical operator order for grouped queries (Phase 2, 3.3).** The pipeline is
`Scan → Filter → ContributionBound → Clamp → [PartitionSelect] → DPAggregate`; the Phase 0
placeholder in `privguard-plan` listed `PartitionSelect` last. §4.5 already says survivors are
noised, and the oracle draws its aggregate noise only for surviving groups (from the
`aggregate` stream), so the survivors must be decided before the aggregate is noised for the
engine to reproduce the oracle bit for bit. No change to what is released: suppressed groups
are dropped and only counted. Scalar queries have no `PartitionSelect`.
