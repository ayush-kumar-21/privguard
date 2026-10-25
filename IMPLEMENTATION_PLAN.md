# PrivGuard — Blueprint Implementation Plan

Tracking document for the re-architecture described in `privguard_blueprint.png`:
*a differentially private query execution engine with budget-aware storage semantics*.

This file is the **single source of truth for progress**. Every phase below is a
checkbox. When resuming work after an interruption, read the **Resume point**
section first, then continue at the first unchecked box.

---

## Resume point

> **Done: Phases 0-4 of `TEAM_PLAN.md`, and most of Phase 5 (bug bash, evaluation).**
> **Left in Phase 5: 5.4 report, 5.6 demo polish, and a repo-wide
> `/code-review` + `/security-review` pass.**

Last updated: 2026-10-21 (Ayush)

The gate demo works: a `SELECT COUNT(*)` from the UI goes gateway -> pgwire -> engine, is charged to
the hash-chained ledger, and a repeat returns the cached answer at zero cost. To run it:
`privguardd load` + `seed-demo`, `privguardd serve`, the gateway with `PRIVGUARD_ENGINE_ADDR`, and
`npm run dev:gateway` (see `gateway/README.md`).

**Verified green as of that date** (`run.bat check`):

| Suite | Command | Result |
|---|---|---|
| DP oracle | `run.bat oracle` | 142 passed |
| Rust engine | `run.bat engine` | all pass, fmt and clippy `-D warnings` clean (on a Windows host two bit-exact libm tests in `privguard-privacy::mechanisms` differ in the last bit; Linux CI is the authority) |
| FastAPI gateway | `run.bat gateway` | 67 passed (the pgwire tests drive the real daemon) |
| Django (legacy) | `run.bat` then browse :8080 | still runs — untouched by the pivot |

### Where to pick up

1. Read `SPEC.md` §5.1 (page layout) — it is frozen and is the contract.
2. Implement `Page` in `engine/crates/privguard-storage/src/page.rs`: header
   encode/decode, slot directory, insert/read/delete by RID, CRC32C checksum.
3. The compile-time layout assertions already in `privguard-storage/src/lib.rs`
   pin the header and slot sizes — build on those rather than restating them.
4. `run.bat engine` after every change; CI gates on fmt and clippy too.

---

## Architecture decision (settled)

The blueprint replaces Django + PostgreSQL with a from-scratch **Rust engine**
plus a **FastAPI** gateway. That is a 14-week build, so we do **not** delete the
working Django stack up front.

**Strategy: build alongside, cut over at the end.**

- `engine/` (Rust) and `gateway/` (FastAPI) grow as new top-level directories.
- `backend/` (Django) keeps running the whole time, so `run.bat` never breaks and
  the project is always demoable.
- `oracle/` holds the Python DP reference implementation that the Rust engine is
  validated against — the blueprint's "REFERENCE + EVAL" row.
- Django is retired only in **Phase 5.5**, once the engine passes the oracle.

### Target layout

```
privguard/
├── engine/          # Rust — the core deliverable, DB internals from scratch
│   └── crates/
│       ├── privguard-storage/   pages · heap files · buffer pool · B+ tree
│       ├── privguard-catalog/   self-hosted catalog
│       ├── privguard-sql/       parser & binder (sqlparser-rs)
│       ├── privguard-plan/      logical planner + Volcano physical plan
│       ├── privguard-privacy/   DP operators · zCDP · rewriter · budget ledger
│       ├── privguard-crypto/    AES-256-GCM columns · blake3 chaining
│       ├── privguard-wire/      pgwire v3 server
│       └── privguardd/          the daemon binary
├── oracle/          # Python 3.11 DP reference implementation + pytest
├── gateway/         # FastAPI · Uvicorn · Pydantic v2 · python-jose · argon2
├── frontend/        # React 18 · Vite · TS · Tailwind · Monaco · react-flow
└── backend/         # Django (legacy — retired in Phase 5.5)
```

### Frozen invariants

| Concern | Decision |
|---|---|
| Privacy unit | `patients.patient_id` (SafeHealth demo dataset) |
| Privacy model | zCDP composition, reported as (ε, δ) |
| Page size | 8 KiB, slotted, RID-addressed |
| Buffer eviction | CLOCK |
| Column encryption | AES-256-GCM |
| Ledger chaining | blake3, append-only, reserve → commit/abort |
| Not in scope | transactions/MVCC, cross-unit joins, crash recovery (stretch) |

---

## Phase 0 — Repo preparation

- [x] 0.1 Remove stray `uv init` scaffolding from `backend/` (`main.py`, `pyproject.toml`, `.python-version`, `README.md`)
- [x] 0.2 Create this tracking document
- [x] 0.3 Record the build-alongside architecture decision
- [x] 0.4 Create top-level `engine/`, `oracle/`, `gateway/` directories
- [x] 0.5 Keep `privguard_blueprint.png` in the repo, and gitignore `data/` + `engine/target/`

---

## Phase 1 — WK 1–2 · Foundation

Goal: spec frozen, toolchain reproducible, a deterministic dataset, and a tested
Python DP oracle to validate every later Rust change against.

- [x] 1.1 Rust toolchain available reproducibly (Docker, no host install required)
- [x] 1.2 Cargo workspace scaffold with all eight crates compiling
- [x] 1.3 **Spec freeze** — `SPEC.md`: SQL subset, privacy semantics, page format, wire scope
- [x] 1.4 Deterministic synthetic dataset generator (SafeHealth schema, seedable)
- [x] 1.5 Python DP oracle package (`oracle/`) extracted from `backend/privacy/`, engine-independent
- [x] 1.6 pytest suite for the oracle — accounting identities, mechanism determinism, sensitivity
- [x] 1.7 `run.bat engine` / `run.bat oracle` commands wired up
- [x] 1.8 CI runs the oracle tests and `cargo test`

---

## Phase 2 — WK 3–5 · Storage + baseline

Goal: a working page-based store with an index, and a **non-private** SQL
executor over it. No DP yet — prove the DB internals first.

- [x] 2.1 8 KiB slotted page layout, RID addressing, insert/read/delete slots
- [x] 2.2 Heap file — page allocation, free-space map, sequential scan
- [x] 2.3 CLOCK buffer pool — pin/unpin, dirty tracking, eviction
- [x] 2.4 B+ tree index — point lookup, range scan, split/merge
- [x] 2.5 Self-hosted catalog persisted in the store itself (`sys_*` tables)
- [x] 2.6 Parser & binder over `sqlparser-rs`, resolving against the catalog
- [x] 2.7 Logical planner — query → logical operator tree
- [x] 2.8 Volcano physical executor — `Scan`, `Filter`, `Aggregate`, `GroupBy`
- [x] 2.9 Loader: synthetic dataset → heap files
- [x] 2.10 Baseline correctness tests vs. the oracle's non-private aggregates

---

## Phase 3 — WK 6–8 · Privacy core

Goal: the three core contributions from the blueprint.

- [x] 3.1 zCDP accounting in Rust, cross-checked against the Python oracle
- [x] 3.2 Noise mechanisms — keyed deterministic stream, Laplace / discrete Laplace / Gaussian
- [x] 3.3 **Physical DP operators**: `Clamp` → `ContributionBound` → `DPAggregate` → `PartitionSelect`
- [x] 3.4 **Budget manager** — hash-chained append-only ledger, reserve → commit/abort (WAL-like)
- [x] 3.5 **Privacy rewriter** — query fingerprinting, policy check, noise cache (defeats repeat-and-average)
- [x] 3.6 Correlation / predicate-overlap detection
- [x] 3.7 Property tests (`proptest`) — budget never over-spends, ledger chain never forks
- [x] 3.8 Differential test: Rust engine vs. Python oracle on identical seeds — 1000 random seeded queries, 0 mismatches (`oracle/tests/golden/diff_queries.json`)

---

## Phase 4 — WK 9–11 · Access + interfaces

- [x] 4.1 RBAC — roles, principals, column grants enforced in the binder
- [x] 4.2 AES-256-GCM column encryption at the access-method layer
- [x] 4.3 pgwire v3 server — startup, auth, simple query, result rows
- [x] 4.4 FastAPI gateway — auth (JWT, python-jose, argon2), query endpoint, budget + audit views
- [x] 4.5 psycopg3 client path: gateway → engine
- [x] 4.6 Frontend: swap CodeMirror → **Monaco** SQL console
- [x] 4.7 Frontend: **react-flow** plan viewer
- [x] 4.8 Frontend: repoint API client at the FastAPI gateway
- [x] 4.9 Policy admin column × role grid against the new catalog

---

## Phase 5 — WK 12–14 · Evaluation

- [x] 5.1 `criterion` benchmarks — DP overhead vs. the non-private baseline
- [x] 5.2 Utility evaluation — error vs. ρ curves, exported for the paper
- [x] 5.3 Attack lab: repeat-and-average demonstrably defeated by the noise cache — v2 against the real engine in `gateway/tests/test_attacks_engine.py` (24 tests)
- [ ] 5.4 Paper / report writing
- [-] 5.5 **Cutover** — retire the Django backend, compose runs engine + gateway + ui. **DECIDED 2026-10-23 (Ayush): do NOT delete `backend/`.** Django stays as the demo stack; the engine and gateway run alongside it.
- [ ] 5.6 Demo polish and final README

---

## Bug log

Bugs found and not yet fixed. Close one by fixing it with a regression test, then tick it here.

- [ ] **Held back from the consolidated dependency PR** (each needs its own change, not a bump): **monaco-editor 0.57** (the SQL console imports `monaco-editor/esm/vs/editor/editor.all` and `basic-languages/pgsql`, which moved) and **tailwindcss 4** (new PostCSS plugin, `@tailwind` directives replaced by `@import "tailwindcss"`, JS config model, changed default borders/shadows/rings: needs visual review). Dependabot is told to ignore both in `.github/dependabot.yml` until then. Tailwind 4 would also clear the dev-only npm audit findings.
- [ ] **P0-2b** (Since the consolidated dependency PR: `npm audit --omit=dev` is clean and the full audit is down to 3 moderate, dev only.) Originally: 5 high + 2 moderate `npm audit` findings remained after `npm audit fix`. All are build-time and come through `tailwindcss` 3.x (`braces`, `micromatch`, `fast-glob`, `chokidar`, `postcss-nested`); the only fix is the Tailwind 3 -> 4 major upgrade. `npm audit --audit-level=high` in CI stays red until it lands. Found 2026-10-21 (Sugeshkumar).

## Phase 4-5 findings (2026-10-21, Ayush)

- **P5-1 FIXED: a crash during a ledger rewrite refunded spent budget.** `write_sys_rows` truncated
  the table file and then rewrote every row; `Engine::persist` does that to the ledger and noise
  cache after every query. A crash or a failing row mid-rewrite left a truncated file, and a prefix
  of a hash chain still verifies, so spent budget was forgiven and the cache emptied (fresh noise to
  average). It is now write-to-temp, fsync, rename (`SPEC.md` A3). Regression tests:
  `privguard-catalog/tests/atomic_write.rs`; two of its three fail on the old code.
- **P5-2 FIXED: a restart reopened the averaging hole.** The noise cache was persisted but the
  predicate-overlap history was not, so after a restart a tautology rewrite or near-duplicate
  predicate set got a second independent noise draw for the same rows. `Engine::open` now rebuilds
  the history from the audit log (`SPEC.md` A4). Regression test:
  `privguardd/tests/engine.rs::the_overlap_guard_survives_a_restart` (fails on the old code).
- Differential test (engine vs oracle, 1000 queries), 4 ledger/engine property tests, 12 parser and
  decoder fuzz properties (100,000 cases each, no panic) and 24 end-to-end attacks: no other defects.
- Sensitivity bounds re-derived by hand against `SPEC.md` 4.3: COUNT = C, SUM = C*M, grouped =
  sqrt(G)*C*(M); Laplace at scale D1/sqrt(2*rho) is eps = sqrt(2*rho) pure DP, hence rho-zCDP;
  Gaussian at sigma = D2/sqrt(2*rho) is exactly rho-zCDP; AVG and grouped queries split rho
  and compose to the charged total. No divergence from the spec.
- **Mironov attack: closed (`SPEC.md` A5).** The float Laplace/Gaussian samplers are replaced by exact
  integer samplers (CKS20) on a grid; released values are `integer * step`. Proved by tests rather than
  asserted: both samplers match their exact pmf, every output lies on the grid for inputs that differ in
  the last float bits, parameters round toward more noise, and the engine agrees with the oracle
  exactly on all 1000 differential queries.
- **Known limitations, not bugs (document in the report):** (1) the exact samplers loop a random
  number of times, so latency is a weak side channel (Jin et al.); the Django path pads responses to a
  floor, the engine does not; (2) budgets are per principal, so two colluding accounts get two independent noise
  draws and their privacy loss composes; (3) `HeapScan` loads the whole table into memory;
  (4) rename is not followed by a directory fsync; (5) startup replays every executed query
  to rebuild the overlap history, O(history x table); (6) a per-stage rho below ~5e-10 is refused.
- **Open question for the team (not changed):** partition selection charges each group as if a unit touched
  one group, though a unit can touch G. The Django backend corrects this (`partition_params`: per-group
  `eps0 = sqrt(2 rho_part / G)`, and a `delta` split across G); the oracle and engine follow the frozen
  SPEC 4.5 and do not. Fixing it changes grouped results and needs a SPEC decision.
- Utility curves: `docs/eval/utility_vs_rho.md`. Benchmarks: `docs/eval/benchmarks.md`.

## Commands

| Command | Does |
|---|---|
| `run.bat` | build + start the legacy Django stack (app on :8080) |
| `run.bat check` | oracle tests + engine build/test — everything needing no container |
| `run.bat oracle` | Python DP oracle test suite (uv; no Docker) |
| `run.bat engine` | build and test the Rust engine in Docker |
| `run.bat data` | regenerate the SafeHealth CSVs into `data/safehealth/` |

Rust needs no host toolchain — `engine/Dockerfile` pins `rust:1.97-slim` and CI
pins the same version, so a green local run means a green CI.

## Conventions

- Rust: `cargo fmt`, `cargo clippy -D warnings`, unit tests beside the code.
  Facts about frozen constants go in `const _: () = assert!(…)` rather than a
  `#[test]`, so a bad constant fails the build instead of a test run.
- Python oracle: `pytest`, type hints, **stdlib only**, and no Django or engine
  imports — it cannot be evidence about the engine if it needs the engine.
- Numbers shared between the two implementations are pinned as golden values in
  both test suites (`test_golden_values_shared_with_the_rust_engine` /
  `golden_values_shared_with_the_python_oracle`).
- Every phase ends with something runnable or testable — never a half-wired layer.
- Update the **Resume point** at the top of this file at the end of every session.

## Session log

**2026-09-03 — Phases 0 and 1.**
Froze `SPEC.md` v1.0. Scaffolded the nine-crate Cargo workspace (dependency-free
by design, so the foundation builds with no network). Landed zCDP accounting in
both Rust and Python and pinned them together with shared golden values.
Extracted the DP oracle out of `backend/privacy/` into a standalone,
Django-free `oracle/` package with a reference executor covering contribution
bounding, clamping, noise and partition selection. Added the deterministic
dataset generator. Wired `run.bat engine|oracle|data|check` and two CI jobs.

Also fixed, incidentally: the api container's entrypoint called
`seed_demo --no-input`, a flag that command does not accept, with the error
swallowed by `2>/dev/null || true` — so the database had never been seeded and
no demo login worked.


## Bug log

| ID | Component | Description | Status |
|---|---|---|---|
| **P1-1** | `backend/api/permissions.py` (`IsClinicianOrDbaOrReadOnly`) | `SAFE_METHODS` (`GET`) are permitted for *any* authenticated user, allowing a `dp_analyst` (`alice`) to read un-noised individual rows from `/api/visits/` and `/api/patients/` at $\rho = 0$. Exposed by `attacks/demo.py` (`run_crud_bypass_probe`). | Open (addressed in Phase 3/4 RBAC + Phase 5 Django retirement) |
| **P1-2** | `backend/privacy/service.py` / `sys_predicate_overlap` | `sys_predicate_overlap` is defined in `backend/audit/models.py` and truncated in `seed_demo`, but `run_private_query()` never populates or checks it. High-Jaccard differencing queries ($Q(D)$ vs $Q(D \setminus \{x\})$, Jaccard $= 599/600 = 0.9983 > 0.8$) execute with `overlap_rows = 0`. Exposed by `attacks/demo.py` (`run_differencing_attack`). | Fixed in the Rust engine (Phase 3: predicate-overlap detection, `privguard-plan::rewriter`); open in the legacy Django stack |
| **P1-3** | `backend/privacy/sql.py` / `SysNoiseCache` | `QuerySpec.canonical()` hashes raw AST filter literals without clamping/domain normalisation or overlap checking. Tautological `WHERE` rewrites (`AND total_charge <= 1000001.0`, `1000002.0`, ...) produce distinct fingerprints, bypassing `SysNoiseCache` memoisation and drawing fresh independent noise on identical row sets. Exposed by `attacks/demo.py` (`run_tautology_averaging_probe`). | Fixed in the Rust engine (Phase 3: noise cache and overlap detection refuse the tautology rewrite); open in the legacy Django stack |
| **P1-4** | `backend/privacy/sql.py` (`_literal_value`) | Negative numeric literals in `WHERE` clauses are parsed by `sqlglot` as `exp.Neg(exp.Literal)` and rejected with HTTP 400 (`unsupported literal`). Rust `privguard-sql` (Task 2.6) handles `UnaryOp::Minus` on numeric literals properly. | Fixed in Rust (`privguard-sql` 2.6); open in legacy Django parser |

**2026-10-21 — Phases 4 and 5 (Ayush), one PR.**
Added the 1000-query engine-vs-oracle differential test (fixture generated by the oracle, replayed
by the Rust executor over real heap files; a perturbed-rho mutation makes all 1000 fail, so the
test can fail). Added proptest properties for the ledger (never over-spends, chain never forks,
any tampering refused) and for the engine (failed queries cost nothing, repeats are free and
identical, also after a restart). Fuzzed the page decoder, row decoder, pgwire parsers and SQL
parser. Attack lab v2 against the real engine. Criterion benchmarks and utility curves. Found and
fixed P5-1 (non-atomic ledger rewrite) and P5-2 (overlap history lost on restart). Cutover, report and demo polish are still open.
