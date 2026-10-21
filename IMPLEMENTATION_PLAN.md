# PrivGuard — Blueprint Implementation Plan

Tracking document for the re-architecture described in `privguard_blueprint.png`:
*a differentially private query execution engine with budget-aware storage semantics*.

This file is the **single source of truth for progress**. Every phase below is a
checkbox. When resuming work after an interruption, read the **Resume point**
section first, then continue at the first unchecked box.

---

## Resume point

> **Done: Phase 0 and Phase 1 (WK 1–2 Foundation).**
> **Next up: Phase 2.1 — the 8 KiB slotted page layout in `privguard-storage`.**

Last updated: 2026-09-03

**Verified green as of that date** (`run.bat check`):

| Suite | Command | Result |
|---|---|---|
| DP oracle | `run.bat oracle` | 93 passed |
| Rust engine | `run.bat engine` | 43 passed, fmt clean, clippy `-D warnings` clean |
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

- [ ] 2.1 8 KiB slotted page layout, RID addressing, insert/read/delete slots
- [ ] 2.2 Heap file — page allocation, free-space map, sequential scan
- [ ] 2.3 CLOCK buffer pool — pin/unpin, dirty tracking, eviction
- [ ] 2.4 B+ tree index — point lookup, range scan, split/merge
- [ ] 2.5 Self-hosted catalog persisted in the store itself (`sys_*` tables)
- [ ] 2.6 Parser & binder over `sqlparser-rs`, resolving against the catalog
- [ ] 2.7 Logical planner — query → logical operator tree
- [ ] 2.8 Volcano physical executor — `Scan`, `Filter`, `Aggregate`, `GroupBy`
- [ ] 2.9 Loader: synthetic dataset → heap files
- [ ] 2.10 Baseline correctness tests vs. the oracle's non-private aggregates

---

## Phase 3 — WK 6–8 · Privacy core

Goal: the three core contributions from the blueprint.

- [ ] 3.1 zCDP accounting in Rust, cross-checked against the Python oracle
- [ ] 3.2 Noise mechanisms — keyed deterministic stream, Laplace / discrete Laplace / Gaussian
- [ ] 3.3 **Physical DP operators**: `Clamp` → `ContributionBound` → `DPAggregate` → `PartitionSelect`
- [ ] 3.4 **Budget manager** — hash-chained append-only ledger, reserve → commit/abort (WAL-like)
- [ ] 3.5 **Privacy rewriter** — query fingerprinting, policy check, noise cache (defeats repeat-and-average)
- [ ] 3.6 Correlation / predicate-overlap detection
- [ ] 3.7 Property tests (`proptest`) — budget never over-spends, ledger chain never forks
- [ ] 3.8 Differential test: Rust engine vs. Python oracle on identical seeds

---

## Phase 4 — WK 9–11 · Access + interfaces

- [ ] 4.1 RBAC — roles, principals, column grants enforced in the binder
- [ ] 4.2 AES-256-GCM column encryption at the access-method layer
- [ ] 4.3 pgwire v3 server — startup, auth, simple query, result rows
- [ ] 4.4 FastAPI gateway — auth (JWT, python-jose, argon2), query endpoint, budget + audit views
- [ ] 4.5 psycopg3 client path: gateway → engine
- [ ] 4.6 Frontend: swap CodeMirror → **Monaco** SQL console
- [ ] 4.7 Frontend: **react-flow** plan viewer
- [ ] 4.8 Frontend: repoint API client at the FastAPI gateway
- [ ] 4.9 Policy admin column × role grid against the new catalog

---

## Phase 5 — WK 12–14 · Evaluation

- [ ] 5.1 `criterion` benchmarks — DP overhead vs. the non-private baseline
- [ ] 5.2 Utility evaluation — error vs. ρ curves, exported for the paper
- [ ] 5.3 Attack lab: repeat-and-average demonstrably defeated by the noise cache
- [ ] 5.4 Paper / report writing
- [ ] 5.5 **Cutover** — retire the Django backend, compose runs engine + gateway + ui
- [ ] 5.6 Demo polish and final README

---

## Bug log

Bugs found and not yet fixed. Close one by fixing it with a regression test, then tick it here.

- [ ] **P0-2b** 5 high + 2 moderate `npm audit` findings remain after `npm audit fix`. All are build-time and come through `tailwindcss` 3.x (`braces`, `micromatch`, `fast-glob`, `chokidar`, `postcss-nested`); the only fix is the Tailwind 3 -> 4 major upgrade. `npm audit --audit-level=high` in CI stays red until it lands. Found 2026-10-21 (Sugeshkumar).

## Commands

| Command | Does |
|---|---|
| `run.bat` | build + start the legacy Django stack (app on :8080) |
| `run.bat check` | oracle tests + engine build/test — everything needing no container |
| `run.bat oracle` | Python DP oracle test suite (uv; no Docker) |
| `run.bat engine` | build and test the Rust engine in Docker |
| `run.bat data` | regenerate the SafeHealth CSVs into `data/safehealth/` |

Rust needs no host toolchain — `engine/Dockerfile` pins `rust:1.83-slim` and CI
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
