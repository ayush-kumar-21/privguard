# PrivGuard Attack Lab (`attacks/`)

Empirical adversarial evaluation of PrivGuard's differential-privacy defences.

## Scenarios & Live Results (Phase 1 — Legacy Django Gateway)

| Attack / Probe | Target property | Raw Postgres (`:55000` / `:5432`) | PrivGuard API (`:55001` / `:8000`) |
|---|---|---|---|
| **Differencing attack** ($Q(D) - Q(D \setminus \{x\})$) | Reconstruct outlier visit (`visit_id=453`, `$59,958.90`) | `$59,958.90` (err `$0.00` -> **LEAKED**) | `$-4,027,388.18` (err `$4,087,347.08` -> **MASKED**; `overlap_rows=0` `[HOLE P1-2]`) |
| **Repeat-and-average** ($N=10$ identical queries) | Cancel zero-mean noise via repeated queries | `$5,736,206.22` (err `$0.00` -> **LEAKED**) | `$7,185,892.08` (err `$1,449,685.86`, `std=$0.00`, `cache_hits=9/9` -> **BLOCKED**) |
| **Tautology rewrite** ($k=5$ shifted bounds) | Bypass AST-hash memoisation via `total_charge <= 1000000 + k` | `$5,736,206.22` (err `$0.00` -> **LEAKED**) | `std=$2,008,915.55` across `5/5` fresh draws (avg err `$66,225.63` -> **BYPASSES CACHE** `[HOLE P1-3]`) |
| **REST CRUD read** (`GET /api/visits/`) | Role enforcement on raw clinical tables | `$59,958.90` (`visit_id=453`) | `$59,958.90` (err `$0.00`, `rho=0.0` -> **EXACT LEAK TO `dp_analyst`** `[HOLE P1-1]`) |

## Exposed Holes Logged in `IMPLEMENTATION_PLAN.md`

* **P1-1 (`backend/api/permissions.py`)**: `IsClinicianOrDbaOrReadOnly` permits `SAFE_METHODS` (`GET`) for any authenticated user, allowing a `dp_analyst` (`alice`) to read un-noised rows from `/api/visits/` and `/api/patients/` at $\rho = 0$.
* **P1-2 (`backend/privacy/service.py`)**: `sys_predicate_overlap` exists in the schema (`backend/audit/models.py`) but is never populated or checked during query execution; high-Jaccard differencing queries (`599/600` rows, $\text{Jaccard} = 0.9983$) execute without overlap warnings or rejection.
* **P1-3 (`backend/privacy/sql.py`, `backend/privacy/service.py`)**: `QuerySpec.canonical()` hashes syntactic predicates without semantic bound normalisation or overlap detection, allowing tautological `WHERE` rewrites (`total_charge <= 1000001.0`, `1000002.0`, ...) to bypass `SysNoiseCache` and average independent noise draws.
* **P1-4 (`backend/privacy/sql.py`)**: `_literal_value()` only accepts `exp.Literal` and rejects negative numeric literals (`exp.Neg`, e.g. `WHERE total_charge >= -1.0`) with HTTP 400.

## Attack lab v2 (Phase 4, real engine)

`gateway/tests/test_attacks_engine.py` replays the attacks against `privguardd` through the gateway,
each against a fresh engine, and runs in CI. Every attack is expected to fail:

| Attack | Defence checked |
|---|---|
| Differencing (`Q(D)` minus `Q(D` without a few rows`)`) | overlap detector refuses a near-duplicate predicate set (403), and the refusal costs no budget |
| Repeat-and-average (15 identical queries) | one answer, one charge; repeats are cache hits |
| Tautology rewrite (`total_charge <= 1000000 + k`) | same rows, different fingerprint: refused by overlap, so at most one noise draw |
| Restart, then rewrite (`privguardd/tests/engine.rs`) | overlap history is rebuilt from the audit log, so the rewrite is still refused |
| Budget exhaustion | the analyst is cut off (429) at the allowance; spent never exceeds total; ledger verifies |
| SQL injection and non-aggregate SQL (10 forms) | refused with 400/403, no charge, engine unaffected |
| Quote and comment smuggling | treated as a literal / plain `COUNT(*)`; no widened predicate |
| Ungranted column, no-budget principal, revoked grant | refused (403), revocation is immediate |
| JWT forgery (wrong key, edited payload, refresh-as-access) | 401 |
| Analyst on admin endpoints, cross-principal reads | 403 / own rows only |

Phase 1's `demo.py` above targets the legacy Django stack; the holes it logged (P1-1..P1-3) do not
exist in the engine path.

## Usage

```bash
python attacks/demo.py
```

* Auto-discovers PostgreSQL on `PRIVGUARD_DB_PORT` (`55000`, fallback `5432`) using a zero-dependency stdlib PostgreSQL v3 wire-protocol (`SCRAM-SHA-256`) client.
* Auto-discovers the PrivGuard API on `PRIVGUARD_API_URL` (`http://127.0.0.1:55001`, fallback `http://127.0.0.1:8000`).
