# PrivGuard — Database Hardening & Implementation Plan

**Scope:** the PostgreSQL layer only — schema integrity, the two-phase budget protocol, query
pushdown, indexes, DB-level security, backup/restore, and tests.
**Audience:** the PrivGuard team (BCSE307L, Dr. Deepika J). Rubric Sections 2, 3, 5, 6 + bonus.
**Baseline reviewed:** commit `c5a17ec`, 28 models across 5 apps, 6 migrations, 0 tests.

Companion docs: `PrivGuard_ER_Documentation.md` (the design), `PrivGuard_Rubric_Alignment.md` (the
contract), `PrivGuard_Project_Plan.md` (the research vision — Future Work).

---

## 0. Executive summary

The schema is *designed* well — 28 entities, 3NF, a hash-chained ledger, a two-phase reserve/commit
protocol, three views. What is missing is everything that makes a schema **hold** under load, failure,
and adversarial input. Concretely, today:

| # | Gap | Evidence | Severity |
|---|---|---|---|
| 1 | **Zero CHECK constraints in the entire database** | `grep -r CheckConstraint backend/` → no matches | **High** — Section 2 explicitly grades CHECK constraints, and the budget invariant `spent + reserved ≤ total` is enforced only inside one PL/pgSQL branch |
| 2 | **The ledger invariant is prose, not code** | ER doc §4 promises `rho_spent == Σ COMMIT`; nothing verifies it | **High** — the one denormalisation the design defends is currently unguarded |
| 3 | **Two-phase protocol is not idempotent and leaks reservations** | `privacy/service.py:25` imports `transaction` and never uses it; three autocommit statements | **High** — a crash after RESERVE permanently burns budget; a retry double-charges |
| 4 | **Contribution bounding runs in Python, not SQL** | `privacy/service.py:129-142`, `163-181` stream every matching row into a `defaultdict` | **High** — O(rows) memory/network; also the weakest answer to "aggregate queries + optimisation" |
| 5 | **Suspended accounts can still spend** | `sp_reserve_budget` never reads `status` (`budget/migrations/0002:132-146`) | **Medium** |
| 6 | **A status value the model does not allow is written on the hot path** | `service.py:249` writes `status="pending"`; `audit/models.py:36-43` has no `pending` choice | **Medium** — blocks adding the CHECK in gap 1 |
| 7 | **4 indexes total**, none covering the columns the DP layer actually filters/groups on | `models.py` `indexes = [...]` | **Medium** |
| 8 | **No DB-level authorization at all** — one superuser-ish role does everything | no `GRANT`/`REVOKE` anywhere | **Medium** — Section 5 is 5 marks and flagged as "where all students make the mistake" |
| 9 | **No backup/restore artifacts** | no `db/`, no `*.sh`, no `schema.sql` | **Medium** — Section 6, and `pg_restore` will silently **rewrite** the ledger hashes as written today |
| 10 | **No tests** — `pytest-django` is in `requirements.txt` and unused | `find . -name 'test*'` → nothing | **Medium** — bonus +1, and nothing protects the refactors below |
| 11 | Dead/lying columns: `est_row_count`, `n_pages`, `root_page_id`, `height` always 0; `is_encrypted=True` on `patients.mrn` while nothing encrypts | `seed_demo.py:103`, `catalog/models.py:43-51` | **Low** — but an examiner will ask |
| 12 | Chain depends on `pgcrypto.digest()`, which may not be on the search path on managed Postgres | `budget/migrations/0002:74` | **Low probability, high blast radius** at deploy time |

This plan closes all twelve in six phases. **Phases 1–3 are the graded core; do them in order.**
Nothing here changes the ER model, so Section 2 stays earned as-is.

---

## 1. Design rules for this work

These constrain every change below. Write them into the report — they are viva answers.

1. **The ledger is the source of truth; `sys_budget_account` is a cache.** Every new invariant is
   expressed as "recomputable from `sys_budget_ledger`". This is what makes the ER doc's
   denormalisation defensible.
2. **Never change `fn_ledger_payload`.** Its output is hashed into every existing row; changing the
   concatenated columns invalidates the whole chain retroactively. Changing *procedure parameters* or
   *stored values* is safe (hashes are computed at insert time). If a payload change ever becomes
   unavoidable, add `chain_version smallint` and have `fn_verify_ledger` dispatch on it — do not
   silently break old rows.
3. **Constraints go in the database, not only in Python.** A serializer that validates is convenience;
   a `CHECK` is a guarantee. Where both are cheap, do both.
4. **Analyst SQL never reaches Postgres as SQL.** Unchanged from `privacy/sql.py`. The pushdown in
   Phase 3 is built with ORM expression objects (`Window`, `Least`, `Greatest`), so every literal
   remains a bound parameter. This is non-negotiable — it is the Section 5 SQLi story.
5. **`ATOMIC_REQUESTS` stays `False`.** Wrapping the whole request in one transaction would make
   RESERVE non-durable and collapse the two-phase protocol into one phase. Document the choice
   explicitly; it looks like an omission otherwise.

---

## 2. Phase 1 — Integrity: constraints the schema is missing

**Goal:** every business rule currently living in a Python `if` or a docstring becomes a DB constraint.
**Effort:** ~5 h. **Owner:** DB lead. **Rubric:** Section 2 (constraints), Section 3.

### 1.1 Fix the status enum first (blocker)

`privacy/service.py:249` inserts `status="pending"`, and `service.py:266/281` can leave it as
`"rejected_policy"`. Add the two missing values to `audit/models.py` **before** adding any CHECK, or
the CHECK will reject the hot path:

```python
STATUS_CHOICES = [
    ("pending", "pending"),          # NEW — written at service.py:247 before RESERVE
    ("executed", "executed"),
    ("cache_hit", "cache_hit"),
    ("error", "error"),              # NEW — terminal state for the reaper (§3.4)
    ("rejected_budget", "rejected_budget"),
    ("rejected_policy", "rejected_policy"),
    ("rejected_correlation", "rejected_correlation"),
    ("suppressed_partition", "suppressed_partition"),
]
```

### 1.2 Migrations to add

One migration per app, all reversible, all using `models.CheckConstraint` / `UniqueConstraint` so they
appear in `sqlmigrate` output for the report.

**`budget/migrations/0003_budget_constraints.py`** — the ones that matter most:

```python
constraints = [
    CheckConstraint(check=Q(rho_total__gt=0),      name="ck_account_rho_total_pos"),
    CheckConstraint(check=Q(rho_spent__gte=0),     name="ck_account_rho_spent_nonneg"),
    CheckConstraint(check=Q(rho_reserved__gte=0),  name="ck_account_rho_reserved_nonneg"),
    # THE budget invariant — currently enforced only inside sp_reserve_budget's IF.
    CheckConstraint(
        check=Q(rho_spent__lte=F("rho_total") - F("rho_reserved")),
        name="ck_account_never_oversold",
    ),
    CheckConstraint(check=Q(delta_target__gt=0) & Q(delta_target__lt=1),
                    name="ck_account_delta_in_unit_interval"),
    # 'refill' window without a period is meaningless
    CheckConstraint(check=~Q(window_type="refill") | Q(refill_period_s__gt=0),
                    name="ck_account_refill_needs_period"),
]
```

For `BudgetLedger` (raw SQL, because `octet_length` has no ORM lookup):

```sql
ALTER TABLE sys_budget_ledger
  ADD CONSTRAINT ck_ledger_hash_len
  CHECK (octet_length(prev_hash) = 32 AND octet_length(entry_hash) = 32);
```

This is the constraint that makes `BinaryField(max_length=32)` mean something — Django's `max_length`
is ignored for `bytea`.

**Settle-once / reserve-once, as partial unique indexes** (same migration). These enforce idempotency
structurally, so a duplicated `CALL` from a retried HTTP request cannot double-charge:

```sql
CREATE UNIQUE INDEX uq_ledger_reserve_once
  ON sys_budget_ledger (query_id) WHERE op = 'RESERVE';
CREATE UNIQUE INDEX uq_ledger_settle_once
  ON sys_budget_ledger (query_id) WHERE op IN ('COMMIT', 'ABORT');
```

A partial unique index is a genuinely advanced feature enforcing a genuine invariant — call it out in
the report next to the trigger and the procedures.

**`catalog/migrations/0002_policy_constraints.py`**

```python
# SysColumnPrivacy — moves the runtime error at service.py:100 into the schema
CheckConstraint(
    check=(Q(lower_clamp__isnull=True) & Q(upper_clamp__isnull=True))
          | (Q(lower_clamp__isnull=False) & Q(upper_clamp__isnull=False)
             & Q(lower_clamp__lte=F("upper_clamp"))),
    name="ck_colpriv_clamp_pair_ordered",
),
CheckConstraint(check=Q(max_contrib_per_unit__gte=1), name="ck_colpriv_contrib_pos"),
CheckConstraint(check=Q(min_partition_size__gte=0),   name="ck_colpriv_min_partition_nonneg"),
# SysTablePrivacy
CheckConstraint(check=Q(max_rows_per_unit__gte=1),   name="ck_tabpriv_max_rows_pos"),
CheckConstraint(check=Q(max_groups_per_unit__gte=1), name="ck_tabpriv_max_groups_pos"),
CheckConstraint(check=Q(min_partition_rho__gt=0),    name="ck_tabpriv_min_rho_pos"),
# SysColumn
CheckConstraint(check=Q(ordinal__gte=1), name="ck_column_ordinal_pos"),
```

**`accounts/migrations/0002_grant_constraints.py`**

```python
CheckConstraint(check=Q(rho_multiplier__gt=0), name="ck_grant_rho_multiplier_pos"),
# an expiring attribute grant must expire after it was granted
CheckConstraint(check=Q(expires_at__isnull=True) | Q(expires_at__gt=F("granted_at")),
                name="ck_principal_attr_expiry_after_grant"),
```

**`audit/migrations/0002_audit_constraints.py`**

```python
CheckConstraint(check=Q(times_seen__gte=1),        name="ck_fingerprint_times_seen_pos"),
CheckConstraint(check=Q(rho_charged__gte=0),       name="ck_query_rho_nonneg"),
CheckConstraint(check=Q(jaccard_est__gte=0) & Q(jaccard_est__lte=1),
                name="ck_overlap_jaccard_unit"),
CheckConstraint(check=Q(symmetric_diff_est__gte=0), name="ck_overlap_symdiff_nonneg"),
# Canonical ordering: without this, (a,b) and (b,a) are two rows and
# uq_overlap_pair_principal does not actually deduplicate the pair.
CheckConstraint(check=Q(fp_a__lt=F("fp_b")), name="ck_overlap_canonical_order"),
```

That last one is a real bug fix, not decoration: `SysPredicateOverlap`'s unique constraint
(`audit/models.py:120-123`) is defeated today by inserting the mirrored pair.

**`safehealth/migrations/0002_app_constraints.py`**

```python
# Patient
CheckConstraint(check=Q(birth_year__gte=1900) & Q(birth_year__lte=2100),
                name="ck_patient_birth_year_range"),
UniqueConstraint(fields=["mrn"], name="uq_patient_mrn"),   # MRN is the natural key
# Visit
CheckConstraint(check=Q(discharge_ts__isnull=True) | Q(discharge_ts__gte=F("admit_ts")),
                name="ck_visit_discharge_after_admit"),
CheckConstraint(check=Q(total_charge__gte=0), name="ck_visit_charge_nonneg"),
# Diagnosis / Hospital / others
CheckConstraint(check=Q(severity__isnull=True) | (Q(severity__gte=1) & Q(severity__lte=5)),
                name="ck_diagnosis_severity_range"),
CheckConstraint(check=Q(bed_count__gt=0), name="ck_hospital_bed_count_pos"),
UniqueConstraint(fields=["region_name"], name="uq_region_name"),
UniqueConstraint(fields=["hospital", "dept_name"], name="uq_dept_per_hospital"),
```

`uq_patient_mrn` and `uq_dept_per_hospital` also make `seed_demo` genuinely idempotent — today
`_appdata` (`seed_demo.py:141-149`) `bulk_create`s regions/hospitals/departments unconditionally, so a
second run without `--flush` would duplicate them if it ever got past the `Visit.objects.exists()`
guard.

### 1.3 Column type upgrades

`audit/models.py:59` (`plan_json`) and `:84` (`cached_result`) are `TextField` holding JSON that
`service.py:239` immediately `json.loads`. Switch both to `models.JSONField` → `jsonb`: the database
validates the JSON, and cached results become queryable (useful for the Attack Lab). Migration must
include a `RunPython` cast (`USING cached_result::jsonb`) — trivial on demo data, but write the reverse.

### Acceptance

- `python manage.py makemigrations --check --dry-run` → "No changes detected".
- `python manage.py migrate` then `migrate budget 0002 && migrate budget 0003` → both directions clean.
- `pytest backend/tests/test_constraints.py` (Phase 6) — every constraint above has a test that inserts
  violating data and expects `IntegrityError`.
- `docs/db/constraints.md` — one table: constraint name → rule → why → rubric line.

---

## 3. Phase 2 — Making the budget protocol actually safe

**Goal:** the ledger invariant becomes executable; reservations cannot leak; calls are idempotent;
suspended accounts cannot spend.
**Effort:** ~8 h. **Owner:** DB lead + backend lead. **Rubric:** Section 3 (transactions, procedures,
triggers), bonus best-practice.

### 3.1 Harden the two procedures (`budget/migrations/0004_budget_protocol_v2.py`)

`CREATE OR REPLACE` both procs. Four changes to `sp_reserve_budget`:

```plpgsql
-- (a) read status in the locking SELECT
SELECT rho_total, rho_spent, rho_reserved, status, principal_id
  INTO v_total, v_spent, v_reserved, v_status, v_owner
  FROM sys_budget_account WHERE account_id = p_account_id FOR UPDATE;

-- (b) a suspended or exhausted account cannot reserve  [closes gap 5]
IF v_status <> 'active' THEN
    p_status := 'account_' || v_status;   -- 'account_suspended' | 'account_exhausted'
    RETURN;
END IF;

-- (c) the query must belong to the account's principal.
--     The proc is the security boundary once EXECUTE is granted (Phase 5) —
--     it must not trust the caller to have picked the right account.
IF NOT EXISTS (SELECT 1 FROM sys_query q
                WHERE q.query_id = p_query_id AND q.principal_id = v_owner) THEN
    p_status := 'principal_mismatch';
    RETURN;
END IF;

-- (d) idempotency: a retried HTTP request must not reserve twice
IF EXISTS (SELECT 1 FROM sys_budget_ledger
            WHERE query_id = p_query_id AND op = 'RESERVE') THEN
    p_status := 'already_reserved';
    RETURN;
END IF;
```

`sp_settle_budget` gains the mirrored `already_settled` guard, plus:

- **Pass the mechanism parameters through.** Line 222 of migration 0002 hardcodes `0, 0` for
  `sensitivity` and `sigma_or_scale`, so the COMMIT row — the one an auditor reads — has no record of
  how the noise was calibrated. Add `p_sensitivity` and `p_sigma_or_scale` IN params and store them.
  Safe: this changes stored values, not the payload structure (rule 2).
- **Clear `exhausted` on the way back down.** Today `status` is set to `'exhausted'` and there is no
  code path that ever resets it.

**New: `sp_refill_budget(p_account_id, p_rho_amount)`.** `window_type`, `refill_period_s` and the
`REFILL` ledger op are all dead today — declared and never written. ~30 lines makes them real:
decrement `rho_spent`, reset `status` to `'active'`, write a `REFILL` ledger row (negative
`rho_delta`), advance `window_start`. This also completes the 4-value `op` enum, which otherwise
invites the question "why is one branch of your enum unreachable?"

### 3.2 The ledger invariant, as SQL (`budget/migrations/0005_reconciliation.py`)

The ER doc (§4, line 229) states the invariant and calls it the justification for the one
denormalisation. Make it executable:

```sql
CREATE OR REPLACE FUNCTION fn_reconcile_account(p_account_id integer)
RETURNS TABLE(rho_spent_cached double precision,
              rho_spent_ledger double precision,
              rho_reserved_cached double precision,
              rho_reserved_ledger double precision,
              drift double precision,
              chain_ok boolean)
LANGUAGE sql STABLE AS $$
WITH l AS (
    SELECT
        COALESCE(SUM(rho_delta) FILTER (WHERE op = 'COMMIT'), 0)
      + COALESCE(SUM(rho_delta) FILTER (WHERE op = 'REFILL'), 0) AS spent,
        -- open reservations = RESERVEd queries with no settling row
        COALESCE(SUM(rho_delta) FILTER (
            WHERE op = 'RESERVE' AND NOT EXISTS (
                SELECT 1 FROM sys_budget_ledger s
                 WHERE s.query_id = sys_budget_ledger.query_id
                   AND s.op IN ('COMMIT', 'ABORT'))), 0) AS reserved
    FROM sys_budget_ledger WHERE account_id = p_account_id)
SELECT a.rho_spent, l.spent, a.rho_reserved, l.reserved,
       abs(a.rho_spent - l.spent) + abs(a.rho_reserved - l.reserved),
       (SELECT ok FROM fn_verify_ledger(p_account_id))
FROM sys_budget_account a, l WHERE a.account_id = p_account_id;
$$;
```

Paired with a view and a command:

```sql
CREATE OR REPLACE VIEW v_ledger_reconciliation AS
SELECT a.account_id, p.username AS principal_name, d.db_name, r.*
  FROM sys_budget_account a
  JOIN sys_principal p ON p.id = a.principal_id
  JOIN sys_database  d ON d.db_id = a.db_id
 CROSS JOIN LATERAL fn_reconcile_account(a.account_id) r;
```

`python manage.py verify_budgets [--strict]` reads the view, prints a table, and exits non-zero if any
`drift > 1e-9` or `chain_ok = false`. Run it in CI after the seed, and after every restore (§6.2).
**This is the single highest-value item in the plan** — it turns the project's central claim ("privacy
is enforced by the system, not trusted") into something demonstrable in one command.

### 3.3 Repair the transaction boundaries in `privacy/service.py`

Current shape (`run_private_query`, lines 246-298) is five independent autocommit units:
`INSERT sys_query` → `CALL reserve` → ORM aggregate → `CALL settle` → `INSERT cache` + `UPDATE query`.

Two problems and their fixes:

1. **RESERVE must stay its own transaction** — that is the point of two-phase, and the `FOR UPDATE`
   lock must be released before the (potentially slow) aggregate runs. Keep it, and *say so in a
   comment*, because right now it reads like an oversight. `transaction` is imported at line 25 and
   never used; either use it or drop the import.
2. **Everything after the aggregate must be atomic.** A failure between `CALL settle` and the cache
   insert leaves budget charged with no cached answer, and a `pending` audit row forever:

```python
with transaction.atomic():
    _call_status("CALL sp_settle_budget(...)", [...])   # committed together with:
    SysNoiseCache.objects.create(...)
    fingerprint.times_seen = F("times_seen") + 1        # also fixes the read-modify-write
    fingerprint.save(update_fields=["times_seen"])
    q.status, q.rho_charged, q.latency_ms = "executed", rho_req, elapsed
    q.save(update_fields=[...])
```

Note `times_seen` (line 291-292) is a lost-update race as written — two concurrent identical queries
both read the same value. `F("times_seen") + 1` makes it an atomic DB increment.

Also wrap the `except` path (lines 277-282) so the ABORT and the status write commit together, and
broaden it: `except Exception` currently re-raises after aborting, which is right, but DRF will 500.
Map `DPQueryError` to 400 (already done in `api/views.py:167`) and everything else to a `500` with the
query marked `error`.

### 3.4 A reaper for leaked reservations

Even with correct boundaries, `SIGKILL` between RESERVE and SETTLE leaks a hold. Add
`python manage.py reap_reservations --older-than 300` (and document a cron/systemd timer for the
deploy): find `sys_query` rows still `pending` past the cutoff whose `query_id` has a `RESERVE` and no
settling row, `CALL sp_settle_budget(..., p_committed => false)`, and set the query to `error`. The
partial index from §1.2 makes the lookup exact, and §3.2's reconciliation proves it worked.

### Acceptance

- `tests/test_two_phase.py`: reserve→commit arithmetic; reserve→abort restores exactly; over-budget
  returns `rejected_budget` and writes **no** ledger row; second `CALL` returns `already_reserved` /
  `already_settled`; suspended account is refused.
- `tests/test_two_phase.py::test_concurrent_reserve_does_not_oversell` — two real connections
  (`@pytest.mark.django_db(transaction=True)` + threads) racing on an account with room for one query;
  exactly one succeeds, and `ck_account_never_oversold` is never violated.
- `tests/test_reconciliation.py`: inject drift with a direct `UPDATE sys_budget_account`, assert
  `fn_reconcile_account` reports it and `verify_budgets --strict` exits 1.
- `manage.py reap_reservations` on a manufactured leak restores `rho_reserved` to 0.

---

## 4. Phase 3 — Push the DP aggregate into PostgreSQL

**Goal:** contribution bounding, clamping, and aggregation execute in the database; Python receives
scalars, not rows.
**Effort:** ~10 h (the most delicate change here). **Owner:** backend lead. **Rubric:** Section 3
(aggregate queries, joins, ORM integration, indexes for performance).

### 4.1 The problem

`_execute_scalar` (`service.py:121-155`) pulls `values_list(patient_id, col)` for every matching visit
and enforces the per-unit row cap `C` in a Python `defaultdict`. `_execute_grouped` (`:158-205`) does
the same plus the L0 group cap. At the demo scale (150 patients × 4 visits = 600 rows) this is
invisible. At 50 000 patients it is 200 000 rows over the wire per query, and it is the wrong answer to
"demonstrate aggregate queries and query optimisation" — the database does nothing but `SELECT *`.

### 4.2 The fix: window functions, still through the ORM

The contribution bound *is* `ROW_NUMBER() OVER (PARTITION BY privacy_unit ORDER BY pk) <= C`. The L0
group bound *is* `DENSE_RANK() OVER (PARTITION BY privacy_unit ORDER BY group_col) <= G`. Clamping *is*
`LEAST(GREATEST(col, lo), hi)`. Django 4.2+ supports filtering on window functions (Django 5.1 is
pinned in `requirements.txt`), so this stays entirely in ORM expressions — every literal is a bound
parameter, so rule 4 holds and the SQLi posture is unchanged:

```python
from django.db.models import Count, F, Sum, Value, Window
from django.db.models.functions import DenseRank, Greatest, Least, RowNumber

def _bounded_rows(spec, policy):
    """One queryset that applies the L-inf and L0 contribution bounds in SQL."""
    qs = Visit.objects.filter(_build_q(spec.filters))
    partition = [F(PRIVACY_UNIT)]
    if spec.group_by:
        qs = qs.annotate(grp_rank=Window(
            expression=DenseRank(),
            partition_by=partition,
            order_by=F(spec.group_by).asc(),
        )).filter(grp_rank__lte=policy.max_groups)          # L0 bound (G)
        partition = partition + [F(spec.group_by)]
    return qs.annotate(rn=Window(
        expression=RowNumber(),
        partition_by=partition,
        order_by=F("visit_id").asc(),
    )).filter(rn__lte=policy.max_rows)                      # L-inf bound (C)


def _true_aggregate(spec, policy):
    """Returns the *unnoised* aggregate(s). The only DB round trip."""
    rows = _bounded_rows(spec, policy)
    clamped = Least(Greatest(F(spec.column), Value(policy.lo)), Value(policy.hi)) \
              if spec.column else None
    aggs = {"n": Count("visit_id")}
    if spec.column:
        aggs["total"] = Sum(clamped)
    if spec.group_by:
        aggs["units"] = Count(PRIVACY_UNIT, distinct=True)   # partition-selection signal
        return list(rows.values(spec.group_by).annotate(**aggs).order_by(spec.group_by))
    return rows.aggregate(**aggs)
```

Noise, partition selection (`tau`), and the budget split stay exactly where they are — only the *true*
aggregate moves. `_execute_scalar` / `_execute_grouped` become thin wrappers over `_true_aggregate`.

### 4.3 De-risking the refactor

This changes the numbers users see if it changes semantics anywhere, so:

1. **Write the golden test first.** `tests/test_dp_pushdown.py` seeds a fixed dataset and asserts the
   Python path and the SQL path produce **identical true aggregates** for a matrix of
   (agg × filters × group_by × C × G). Keep the Python implementation in the tree as
   `_true_aggregate_reference()` for exactly this purpose, and mark the test as the contract.
2. **Watch two semantic traps.** (a) `ORDER BY visit_id` must match the old `.order_by("visit_id")`
   (`service.py:123`) or a different *subset* of rows survives the cap — same count, different sum.
   (b) The old grouped path admits a unit's group on first sight and caps at `G` *seen* groups
   (`:167-172`); `DENSE_RANK` ordered by the group column picks the `G` *smallest* groups. These
   differ. Pick `DENSE_RANK` (deterministic and reproducible, which matters for the noise cache) and
   note the change in the test.
3. **`NULL` handling.** `clamp(float(val or 0.0), ...)` (`:135`) maps `NULL` → 0 and *counts* the row;
   `Sum` skips `NULL`. Use `Coalesce(F(col), Value(0.0))` inside the clamp to preserve behaviour.

### 4.4 While in here: two adjacent fixes

- **`_ensure_fact_table` (`service.py:307-309`) writes to the catalog from a read path** and races —
  two first-time queries both `CREATE`. Move catalog seeding entirely into `seed_demo`/a migration and
  make an unknown table a `DPQueryError`. Same for the `get_or_create` at `:221` — wrap in
  `try/except IntegrityError` + re-fetch, since `ast_hash` is unique and concurrent identical queries
  will collide.
- **`rows_scanned` is always `None`** (`service.py:297`). With the pushdown, set it from the `n`
  returned by the bounded aggregate. It is displayed in `v_query_audit` and the UI; a permanently-null
  column in an audit log is a bad look.

### Acceptance

- `tests/test_dp_pushdown.py` green across the matrix.
- `EXPLAIN (ANALYZE, BUFFERS)` for the five benchmark queries shows an `Index Scan` /
  `WindowAgg` plan with no `Seq Scan` on `visits` for filtered queries.
- Rows transferred per DP query ≤ number of output groups (verify with
  `django.db.connection.queries` in a test).

---

## 5. Phase 4 — Indexes, statistics, and evidence

**Effort:** ~4 h. **Rubric:** Section 3 (indexes for performance), bonus best-practice.

### 5.1 Indexes to add (`*/migrations/000N_indexes.py`)

Chosen from what the code actually queries — `privacy/sql.py`'s `FILTERABLE`/`GROUPABLE` sets and
`api/views.py`'s `filterset_fields`/`search_fields`/`ordering_fields`. Django already indexes every FK,
so nothing below duplicates one.

| Index | Justification (cite the call site) |
|---|---|
| `visits (patient_id, visit_id)` | Exactly the `PARTITION BY … ORDER BY` of §4.2 — lets Postgres feed `WindowAgg` pre-sorted and skip the sort node. Supersedes `idx_visits_patient`. |
| `visits (admission_type)` and `visits (dept_id, admit_ts)` | `GROUPABLE = {admission_type, dept_id}`, `FILTERABLE` includes `admit_ts` (`privacy/sql.py:30-32`) |
| `visits (total_charge)` | `FILTERABLE`, and `ordering_fields` in `VisitViewSet` |
| `patients (zip3)`, `patients (insurance_type)` | `PatientViewSet.search_fields` / `filterset_fields` |
| `diagnoses (icd10_code)`, `diagnoses (visit_id, is_primary)` | `DiagnosisViewSet.filterset_fields`; the Attack Lab's rare-code query |
| `lab_results (visit_id, test_code)`, `lab_results (taken_at)` | `LabResultViewSet.filterset_fields` |
| `sys_query (status, submitted_at DESC)` | `QueryAuditViewSet` filters on `status` and orders by `-submitted_at` (`views.py:144-149`) |
| `sys_query (submitted_at) WHERE status = 'pending'` — **partial** | the reaper's exact predicate (§3.4); stays tiny |
| `sys_budget_ledger (query_id)` | `fn_reconcile_account`'s `NOT EXISTS` subquery, and §1.2's guards |
| `sys_budget_ledger (logged_at)` | burn-down chart time-range scans (`frontend/src/components/charts/BudgetBurndown.tsx`) |
| `sys_query_fingerprint (table_id, agg_kind)` | fingerprint analytics / Attack Lab |

**Do not** add an index for `search_fields` `icontains` patterns and claim it helps — a B-tree does not
serve `LIKE '%x%'`. If leading-wildcard search matters, use `pg_trgm` + a GIN index and say why;
otherwise leave it and note the limitation. Examiners notice unjustified indexes more than missing ones.

### 5.2 Make the vestigial catalog columns honest

`root_page_id`, `n_pages`, `est_row_count`, `height` (`catalog/models.py:43-45`, `:108-111`) are
always 0 — leftovers from the Rust-engine design that the ER doc keeps for figure fidelity. Rather than
defend zeros, populate them from Postgres's own statistics:

```python
# manage.py refresh_catalog_stats
ANALYZE;
UPDATE sys_table t SET est_row_count = c.reltuples::bigint, n_pages = c.relpages
  FROM pg_class c WHERE c.relname = t.table_name AND c.relkind = 'r';
UPDATE sys_index i SET height = COALESCE(pg_relation_size(c.oid) / 8192, 1)  -- see note
  FROM pg_class c WHERE c.relname = i.index_name;
```

For `height`, prefer the real B-tree height from `pgstatindex` (`pgstattuple` extension) if available,
else document the approximation. Now `SysTable.est_row_count` is a live catalog statistic — a much
better story than "these columns are for the ER diagram".

### 5.3 Benchmark harness (the evidence)

`python manage.py db_bench --scale 150,5000,50000 --out docs/db/benchmarks.md` runs `EXPLAIN (ANALYZE,
BUFFERS, FORMAT JSON)` on five representative statements — scalar COUNT, filtered SUM, grouped COUNT,
the audit view, the reconciliation view — at three dataset sizes, before and after the Phase-4 indexes,
and writes a markdown table with planning/execution time and rows read. Two graded claims come straight
out of that file: "indexes for performance" (Section 3) and "performance-optimised indexes" as the
bonus best practice.

### 5.4 `seed_demo` must survive the large scales

`seed_demo.py` builds all visits in one Python list (`:167-176`) and then does
`list(Visit.objects.select_related("patient").all())` (`:179`) — every visit in memory twice. At 50 000
patients that is 200 000 model instances plus 200 000 more. Fix:

- Generate and `bulk_create` in `batch_size` chunks inside the loop rather than accumulating.
- Replace `:179` with `.values_list("visit_id", "patient_id", "admit_ts").iterator(chunk_size=5000)`.
- Add `--scale {demo,medium,large}` presets that map to the benchmark sizes.
- Consider `cursor.copy()` (psycopg 3 `COPY`) for the largest scale — 10–20× faster than
  `bulk_create`, and a legitimate "bulk load" talking point.

---

## 6. Phase 5 — Database-level security

**Effort:** ~6 h. **Rubric:** Section 5 (5 marks, flagged as the common failure point).

Today all authorization is Django-side (`api/permissions.py`, `_scoped()` at `views.py:127-130`) and
one DB role owns everything. That is the exact gap the rubric warns about: an SQLi or a compromised
API process has unrestricted DDL.

### 6.1 Least-privilege roles (`db/roles.sql` + `budget/migrations/000N_grants.py`)

```sql
-- Owner role: runs migrations only. Never used by the running API.
-- Runtime role: no DDL, no ledger mutation, execute-only on the protocol.
CREATE ROLE privguard_api LOGIN PASSWORD :'api_password';
GRANT CONNECT ON DATABASE safehealth TO privguard_api;
GRANT USAGE  ON SCHEMA public TO privguard_api;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO privguard_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO privguard_api;

-- The ledger is append-only at the privilege layer too, not just via trg_ledger_readonly.
REVOKE UPDATE, DELETE, TRUNCATE ON sys_budget_ledger FROM privguard_api, PUBLIC;
GRANT  INSERT, SELECT               ON sys_budget_ledger TO privguard_api;

GRANT EXECUTE ON PROCEDURE sp_reserve_budget, sp_settle_budget, sp_refill_budget TO privguard_api;
GRANT EXECUTE ON FUNCTION  fn_verify_ledger, fn_reconcile_account TO privguard_api;
REVOKE ALL ON FUNCTION fn_ledger_payload(...) FROM PUBLIC;   -- internal to the trigger
```

Two layers of defence on the same invariant — trigger *and* privilege — is a strong, cheap slide.
`DATABASE_URL` in the deployed environment points at `privguard_api`; migrations run under the owner
via a separate `DATABASE_URL_MIGRATE`. Document both in `.env.example`.

### 6.2 Row-Level Security (stretch, +credibility)

Enforce `_scoped()` in Postgres so even a raw connection cannot read another analyst's budget:

```sql
ALTER TABLE sys_budget_account ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_own_account ON sys_budget_account FOR SELECT TO privguard_api
  USING (principal_id = current_setting('privguard.principal_id', true)::int
         OR current_setting('privguard.is_auditor', true) = 'on');
```

**Caveat that must be handled or the feature is worse than nothing:** with `CONN_MAX_AGE > 0`
connections are reused across requests, so the GUC must be set with `SET LOCAL` inside a transaction
per request (middleware), never `SET`. If that is too much for the timeline, mark RLS as documented
future work — an RLS policy keyed off a leaking session variable is a security bug, not a feature.

### 6.3 Stop `is_encrypted` from lying

`seed_demo.py:103` marks `patients.mrn` as `is_encrypted=True`, `sys_key_material` exists, and nothing
encrypts anything. Pick one:

- **Implement (preferred, ~2 h):** `pgp_sym_encrypt`/`pgp_sym_decrypt` (pgcrypto is already installed)
  on `patients.mrn`, key from `.env`, exposed only to roles whose `sys_column_grant.access_mode` is
  `raw`. The catalog flag becomes true, and `sys_key_material` gets its first real row.
- **Or downgrade honestly:** set `is_encrypted=False` in the seed and list column encryption in Future
  Work. This is the pre-agreed first scope cut (CP-ABE/encryption), so it is a legitimate choice — but
  a `True` flag with no encryption behind it is worse than either option.

### 6.4 Session-level guardrails in `settings.py`

`DATABASES` (`privguard/settings.py:82-87`) is bare `env.db()`. Add:

```python
DATABASES["default"].update({
    "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
    "CONN_HEALTH_CHECKS": True,
    "OPTIONS": {
        "application_name": "privguard-api",
        "options": "-c statement_timeout=15000 -c lock_timeout=3000 "
                   "-c idle_in_transaction_session_timeout=30000",
    },
})
# Deliberately NOT set: ATOMIC_REQUESTS. See PrivGuard_DB_Plan.md §1 rule 5 —
# request-scoped transactions would make the RESERVE phase non-durable.
```

`statement_timeout` matters concretely here: an unfiltered `SUM` over a large `visits` table is
reachable from the console, and `lock_timeout` stops a pile-up on `sp_reserve_budget`'s `FOR UPDATE`.
If the deploy target puts pgbouncer in transaction-pooling mode (Supabase, some Render tiers), also set
`DISABLE_SERVER_SIDE_CURSORS = True` — otherwise `.iterator()` from §5.4 fails in production only.

---

## 7. Phase 6 — Durability, restore, and tests

**Effort:** ~8 h. **Rubric:** Section 6 (backup/restore, Docker), bonus (CI/CD, automated testing).

### 7.1 The restore hazard nobody has hit yet

`pg_restore` loads `sys_budget_ledger` via `COPY`, and **row-level triggers fire for `COPY`**. So
`trg_ledger_hash` (`budget/migrations/0002:84-86`) will *recompute* `prev_hash`/`entry_hash` on every
restored row. Today that happens to produce the same bytes — but only because the ledger is append-only
so dump order matches `entry_id` order, and because the payload includes `entry_id` and `logged_at`.
That is luck, not a guarantee, and `--disable-triggers` needs superuser (unavailable on managed
Postgres). Make the trigger idempotent instead:

```plpgsql
-- Only compute when the caller did not supply a hash (normal INSERT path).
-- A restore supplies the original bytes; preserve them so the chain is provably
-- the same chain before and after backup/restore.
IF NEW.entry_hash IS NOT NULL
   AND NEW.entry_hash <> decode(repeat('00', 32), 'hex') THEN
    RETURN NEW;
END IF;
```

The procedures already insert all-zero placeholders (`0002:160-161`, `:223-224`), so the normal path is
unaffected. Verification that this worked is `fn_verify_ledger` post-restore — which is now a test.

Also: `TRUNCATE` does **not** fire the row-level append-only trigger — `seed_demo.py:11` notes this and
relies on it. That is a hole: any role with `TRUNCATE` can erase the ledger. Close it with a
statement-level trigger plus an explicit escape hatch the seeder must opt into:

```plpgsql
CREATE OR REPLACE FUNCTION privguard_ledger_no_truncate() RETURNS trigger AS $$
BEGIN
    IF COALESCE(current_setting('privguard.allow_ledger_truncate', true), 'off') <> 'on' THEN
        RAISE EXCEPTION 'sys_budget_ledger cannot be truncated (set privguard.allow_ledger_truncate)';
    END IF;
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_no_truncate BEFORE TRUNCATE ON sys_budget_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION privguard_ledger_no_truncate();
```

`seed_demo --flush` then issues `SET LOCAL privguard.allow_ledger_truncate = 'on'` in the same
transaction as the `TRUNCATE` (`seed_demo.py:64-67`) — a deliberate, auditable, developer-only bypass.

### 7.2 Backup/restore artifacts

```
db/
  roles.sql          # §6.1, idempotent
  backup.sh          # pg_dump -Fc -f backups/safehealth_$(date +%F_%H%M).dump
  restore.sh         # createdb + pg_restore --clean --if-exists --no-owner
  verify_restore.sh  # restore into safehealth_verify, migrate --check, verify_budgets --strict, dropdb
  schema.sql         # pg_dump --schema-only --no-owner  (regenerated, committed, goes in the report)
```

`verify_restore.sh` is the differentiator: it proves the backup is *usable* and that the hash chain
survives a dump/restore cycle byte-for-byte. Most submissions show `pg_dump` output and stop. Add
`make db-backup` / `make db-restore` / `make db-verify` wrappers and a README section with the exact
commands and expected output.

Also fix `docker-compose.yml`: the `api` service has no migrate/seed step, so a fresh
`docker compose up` starts against an empty schema. Add an entrypoint that runs
`migrate --noinput && seed_demo && refresh_catalog_stats`, and a `db` healthcheck dependency for `ui`.

### 7.3 The test suite (currently zero)

`pytest-django` is already a dependency. Add `backend/pytest.ini` (`DJANGO_SETTINGS_MODULE`,
`--reuse-db`) and `backend/conftest.py` (fixtures: `seeded_db`, `analyst`, `account`, `two_connections`).

| File | Covers | Guards |
|---|---|---|
| `test_constraints.py` | every CHECK/UNIQUE from Phase 1 rejects bad data | Section 2 claim |
| `test_ledger.py` | trigger chains hashes; `UPDATE`/`DELETE`/`TRUNCATE` raise; `fn_verify_ledger` ok; tampering (via a disabled trigger) is caught with the right `first_bad_entry` | the project's central claim |
| `test_two_phase.py` | commit/abort math, over-budget, idempotency, suspended account, **concurrent reserve** | Phase 2 |
| `test_reconciliation.py` | injected drift detected; `verify_budgets --strict` exit code | Phase 2 |
| `test_dp_pushdown.py` | SQL path == reference Python path (matrix) | Phase 3 — write this **first** |
| `test_views.py` | `v_budget_status.rho_remaining` == model property; `v_query_audit`/`v_ledger_reconciliation` join correctly | Section 3 views |
| `test_migrations.py` | `makemigrations --check`; full reverse migration to zero | reversibility |
| `test_restore.py` | dump → restore → `fn_verify_ledger` still ok (marked `slow`) | §7.1 |

CI (`.github/workflows/ci.yml`) with a `postgres:16` service: `migrate`, `seed_demo`, `pytest`,
`verify_budgets --strict`, `makemigrations --check`. That is bonus +1 and Section 6's CI line, and it
means the ledger invariant is checked on every PR.

### 7.4 Swap the digest to a core function (5-minute deploy de-risk)

`trg_ledger_hash` and `fn_verify_ledger` call pgcrypto's `digest(x, 'sha256')`. On managed Postgres,
`pgcrypto` often lands in a non-default schema (Supabase installs it under `extensions`), and
`CREATE EXTENSION` may be refused outright — the trigger then fails at runtime, in production, on the
write path. PostgreSQL 11+ has a **core** `sha256(bytea)` that returns byte-identical output. Replace
both call sites: no extension dependency, **and existing hashes still verify** (rule 2 is respected —
the payload is untouched, only the function name changes). Keep the `CREATE EXTENSION pgcrypto` line
only if §6.3 implements `pgp_sym_encrypt`.

---

## 8. Schema-fidelity cleanups (small, do them opportunistically)

- **Three columns describe the same fact.** `SysTable.privacy_unit_col` (`catalog/models.py:48-52`),
  `SysColumn.is_privacy_unit` (`:82`), and `SysTablePrivacy.privacy_unit_col` (`:155-158`) can disagree
  — a transitive-dependency smell in a schema the ER doc certifies as 3NF. Keep all three for ER
  fidelity (Figures 1 & 5) and add `trg_sync_privacy_unit`: `AFTER INSERT OR UPDATE ON
  sys_table_privacy` → propagate to `sys_table.privacy_unit_col` and `sys_column.is_privacy_unit`.
  One trigger removes the anomaly, gives `sys_table_privacy` a single-writer story, and is a third
  trigger for Section 3. Document it as a *fourth guarded denormalisation* alongside `rho_spent`.
- **`sys_budget_ledger.query_id` nullability disagrees with the ER DDL** (`NOT NULL` in
  `PrivGuard_Project_Plan.md:310`, `null=True` in `budget/models.py:64`). `REFILL` rows legitimately
  have no query, so the model is right and the DDL is stale — fix the DDL listing, and add
  `CHECK (op = 'REFILL' OR query_id IS NOT NULL)`.
- **`updated_at` + a generic touch trigger** on the mutable application tables (`patients`, `visits`,
  `sys_column_grant`, `sys_column_privacy`). Standard practice, cheap, and it makes the audit story on
  policy changes real.
- **`docs/db/normalization.md`** — the ER doc *asserts* 3NF (line 229) without showing the work. Walk
  1NF → 2NF → 3NF for three tables (one associative, one fact, `sys_budget_account`) and list all
  guarded denormalisations with their guarding invariant. Section 2 grades normalisation explicitly.

---

## 9. Sequencing, effort, and ownership

| Phase | Deliverable | Effort | Blocks | Rubric |
|---|---|---|---|---|
| 0 | `.env` from `.env.example`, `docker compose up db`, `migrate`, `seed_demo`, capture baseline `EXPLAIN` | 1 h | everything | — |
| 1 | Constraints + status enum + `jsonb` | 5 h | Phase 2 tests | §2, §3 |
| 2 | Protocol v2, reconciliation, transaction boundaries, reaper | 8 h | — | §3, bonus |
| 3 | SQL pushdown (golden test first) | 10 h | Phase 4 benchmarks | §3 |
| 4 | Indexes, `refresh_catalog_stats`, `db_bench`, seed scaling | 4 h | — | §3, bonus |
| 5 | DB roles/GRANTs, session guardrails, encryption decision | 6 h | deploy | §5 |
| 6 | Idempotent trigger, `db/*.sh`, test suite, CI | 8 h | submission | §6, bonus |
|   | Docs (`constraints.md`, `normalization.md`, `advanced_features.md`, `benchmarks.md`) | 3 h | report | §2, §3 |

**~45 h total**, comfortably inside a 14-week / 2–3 person budget. Suggested split: DB lead takes
1, 2, 5; backend lead takes 3, 4; either takes 6. Phases 1 and 3 can start in parallel *only if* the
golden test (§4.3) lands first.

**Branching:** the pre-commit hook (`.githooks/pre-commit`) blocks direct commits to `main`. Work on
`feature/db-hardening` → PR into `dev` → `dev` into `main`. One commit per numbered sub-item gives
~20 meaningful commits, which also feeds Section 6's ≥10-commit requirement.

---

## 10. Advanced-DB-feature scoreboard (for the report)

What Section 3 asks for, versus what exists after this plan. Cite file and line in the report — the
examiner note about ER-traceability cuts both ways, and precise citations read as authority.

| Feature | Now | After |
|---|---|---|
| Triggers | 2 (`trg_ledger_hash`, `trg_ledger_readonly`) | **5** (+ `trg_ledger_no_truncate`, `trg_sync_privacy_unit`, `trg_touch_updated_at`) |
| Stored procedures | 2 (`sp_reserve_budget`, `sp_settle_budget`) | **3** (+ `sp_refill_budget`), all idempotent and status-aware |
| Functions | 2 (`fn_ledger_payload`, `fn_verify_ledger`) | **3** (+ `fn_reconcile_account`) |
| Views | 3 | **4** (+ `v_ledger_reconciliation`) |
| Transactions | intent only — `transaction` imported, unused | explicit two-phase with documented boundaries + `FOR UPDATE` + a reaper |
| CHECK constraints | **0** | ~25, incl. the budget invariant |
| Partial / unique indexes | 5 unique | + 2 partial unique (settle-once), + 1 partial (reaper) |
| Performance indexes | 4 | ~15, each justified by a call site, with `EXPLAIN ANALYZE` evidence |
| DCL (`GRANT`/`REVOKE`) | **none** | least-privilege runtime role + ledger privilege lockdown |
| Window functions | none | `ROW_NUMBER` / `DENSE_RANK` contribution bounding |
| Backup/restore | **none** | `pg_dump -Fc` + `pg_restore` + **verified** restore incl. chain re-verification |
| Automated tests | **none** | 8 DB-focused suites in CI |

The through-line for the viva: *every one of these enforces a privacy invariant.* Nothing on this list
is a textbook exercise bolted on for marks — which is precisely what the rubric's "advanced database
features where raw SQL is necessary" carve-out is looking for.
