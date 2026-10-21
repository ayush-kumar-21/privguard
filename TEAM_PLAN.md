# PrivGuard — Team Plan (3 people, 6 phases)

How we split the work in [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) across three
people. Task ids like `2.4` refer to that file; **keep ticking boxes there**. This file only
says *who does what, in what order, and when we sync*.

Contract for everything: [`SPEC.md`](SPEC.md) (frozen). The Python `oracle/` is the
reference — if engine and oracle disagree, one is wrong, and a test must prove which.

## Lanes

| Lane | Owner | Owns | Crates / dirs |
|---|---|---|---|
| **A — Storage & Engine** | Gourishankar | pages, heap, buffer pool, B+ tree, catalog, executor, column encryption, pgwire, benchmarks | `privguard-storage`, `-catalog`, `-crypto`, `-wire`, `privguardd` |
| **B — SQL & Privacy** | Ayush | parser/binder, planner, DP operators, budget ledger, rewriter + noise cache, RBAC | `privguard-sql`, `-plan`, `-privacy`, `oracle/` |
| **C — Interfaces, Attacks & Quality** | Sugeshkumar | bug fixes, attack lab, FastAPI gateway, frontend, CI, utility eval, report | `gateway/`, `frontend/`, `backend/`, `attacks/`, `.github/` |

Lane C looks lightest on Rust but carries the bug-fix and verification load, and
the demo people actually see. Rotate if it feels unbalanced after Phase 2.

## Rules that let three people work in parallel

1. **Phase 0 freezes the seams** (traits/types below). After that, lanes build against
   the seam, not each other's code.
2. One branch per task, PR into `main`, one reviewer from a *different* lane.
3. CI must stay green: `cargo fmt`, `clippy -D warnings`, `cargo test`, oracle tests,
   backend tests, frontend type-check.
4. **Every bug fixed gets a regression test in the same PR.** No test, no merge.
5. Sync gates are at the end of each phase: all three lanes demo, then the next phase starts.

---

## Phase 0 — Stabilise & freeze the seams

Goal: green baseline, known bugs closed, interfaces agreed so Phases 1–2 can run in parallel.

| Lane | Tasks |
|---|---|
| **A** | Define `RowSource` iterator trait + `Value`/`Row` types in `privguard-core` (the executor↔operator seam). Define `PageStore` trait (read/write page by id) so the buffer pool and B+ tree can be unit-tested over an in-memory store. |
| **B** | Confirm oracle test suite really runs (see bug **P0-1**). Write golden-value fixtures: 20 queries × fixed seed → expected noisy answers, committed as JSON. This is the engine's acceptance test later. Define the `PrivacyPolicy` + `QueryFingerprint` types. |
| **C** | Fix every item in the **Known bugs** table below. Add `attacks/` skeleton. Make CI run backend, oracle, frontend type-check, and `npm audit --audit-level=high`. |

**Gate:** CI green on `main`; `run.sh` brings the stack up; golden fixtures + both traits merged.

### Known bugs (found 2026-10-21)

| ID | Issue | Fix | Owner |
|---|---|---|---|
| P0-1 | Oracle tests (README says 93) can't be run with host Python: `pytest` missing | `run.sh oracle` uses `uv`; add to CI + README. Verify count matches README | C |
| P0-2 | 4 frontend vulns (2 high, 2 moderate) in `npm audit` | `npm audit fix`; pin; add audit step to CI | C |
| P0-3 | `SECRET_KEY` defaults to `dev-insecure-change-me`; `DEBUG=True` and DB password `privguard` in compose | Settings must **fail to start** when `DEBUG=False` and key is the default. Compose reads from `.env` | C |
| P0-4 | `CheckConstraint(check=…)` deprecated, removed in Django 6.0 | Rewrite migrations to `condition=` | C |
| P0-5 | DB (`:55000`) and API (`:55001`) published on `0.0.0.0` | Bind to `127.0.0.1:` in compose | C |
| P0-6 | CORS allows only `:5173`; UI on `:8080` works only if nginx proxies `/api` | Verify; document or add origin | C |

---

## Phase 1 — Foundations (parallel)

| Lane | Tasks | Done when |
|---|---|---|
| **A** | 2.1 slotted page · 2.2 heap file · 2.3 CLOCK buffer pool | insert/read/delete by RID; scan; eviction under a pool smaller than the file; CRC detects a flipped byte |
| **B** | 2.6 parser & binder (against a *mock* catalog) · 3.1 verify Rust zCDP vs oracle · 3.2 noise mechanisms (keyed deterministic stream) | SPEC §3 SQL subset parses; unsupported SQL gives the SPEC §9 error; Rust noise matches oracle golden values bit-for-bit |
| **C** | **Attack lab v1 against the Django stack**: differencing attack + averaging attack, run on raw Postgres vs the API, prints a comparison table | `python attacks/demo.py` shows the attack succeeding on raw SQL and failing on PrivGuard. Any hole it exposes is logged as a bug |

**Gate:** A shows a page round-trip + eviction test; B shows a parsed query and matching noise values; C shows the attack table.

## Phase 2 — Engine core (parallel)

| Lane | Tasks | Done when |
|---|---|---|
| **A** | 2.4 B+ tree · 2.5 catalog in the store · 2.9 loader (dataset → heap files) | SafeHealth loads into heap files; point + range lookups by index; catalog survives restart |
| **B** | 2.7 logical planner · 3.3 DP operators `Clamp → ContributionBound → DPAggregate → PartitionSelect` over `RowSource` (tested on in-memory rows) · 3.4 budget ledger (reserve → commit/abort, blake3 chain) | operators match oracle on golden fixtures; ledger chain verifies and detects tampering |
| **C** | FastAPI gateway scaffold (4.4): JWT, argon2, endpoints, talking to a **stub engine** returning canned results · frontend repoint (4.8) | gateway passes its own tests against the stub; frontend logs in through it |

**Gate:** each lane demos independently. Nothing integrated yet — that is Phase 3.

## Phase 3 — Integration (the hard sync)

All three work on the same thing: **one query end to end.**

| Lane | Tasks |
|---|---|
| **A** | 2.8 Volcano executor (`Scan`, `Filter`, `Aggregate`, `GroupBy`) wired to heap + index · 2.10 baseline correctness vs oracle non-private aggregates · 4.2 AES-256-GCM columns |
| **B** | Plug DP operators into the real executor plans · 3.5 rewriter: fingerprint → policy check → noise cache · 3.6 predicate-overlap detection · 4.1 RBAC in the binder |
| **C** | 4.3/4.5 integration support: gateway → engine over pgwire (A builds the server, C builds the client path) · 4.6 Monaco console · 4.7 react-flow plan viewer · 4.9 policy grid |

**Gate:** `SELECT COUNT(*) …` from the UI hits the real engine, is charged to the ledger, and a repeat returns the cached answer at zero cost.

## Phase 4 — Verification & evaluation

| Lane | Tasks |
|---|---|
| **A** | 4.3 pgwire hardening · 5.1 `criterion` benchmarks: DP overhead vs non-private baseline · fuzz the page decoder and wire parser |
| **B** | 3.7 `proptest`: budget never over-spends, ledger never forks, noise cache never leaks fresh noise · 3.8 **differential test** engine vs oracle on identical seeds |
| **C** | 5.3 **Attack lab v2 against the new engine** (differencing, averaging, budget exhaustion, ledger tampering, SQL-injection through the gateway, JWT forgery) · 5.2 error-vs-ρ utility curves |

**Gate:** differential test green on ≥1000 random queries; every attack in the lab fails against the engine; benchmark numbers recorded.

## Phase 5 — Bug bash, cutover, ship

Goal: **find and fix every remaining bug**, then retire Django. Exit criteria are in the checklist below, and the phase is not done until every box is ticked.

| Lane | Tasks |
|---|---|
| **A** | Crash/torn-write behaviour documented and tested · memory and file-handle leak check under a long run · fix everything the fuzzers found |
| **B** | Fix everything proptest/differential tests found · review SPEC against implementation and record divergences in SPEC §10 Amendments · re-verify privacy claims (sensitivity bounds) by hand on each aggregate |
| **C** | **Cross-review the other lanes' code** with `/code-review` and `/security-review` · full E2E run through the UI · 5.5 cutover (compose runs engine + gateway + ui; delete `backend/`) · 5.4 report · 5.6 demo polish |

### Bug-bash exit checklist

- [ ] All of Phase 0's bug table closed and regression-tested
- [ ] `cargo clippy -D warnings`, `cargo test`, oracle, gateway, and frontend suites all green in CI
- [ ] Differential test (engine vs oracle) 0 mismatches over 1000+ seeded queries
- [ ] Every attack in `attacks/` fails against the engine, and each is a CI test
- [ ] Fuzzers (page decoder, wire parser, SQL parser) ran ≥ 10 minutes each with no crash
- [ ] `npm audit` / `cargo audit` / `pip-audit`: no high or critical findings
- [ ] No default secrets accepted in non-debug mode; DB/API not exposed beyond localhost in compose
- [ ] Fresh clone → `./run.sh` → login → run a query → see the budget drop, with no manual steps
- [ ] README test counts and status table match reality
- [ ] A bug triage list is empty or each remaining item has a written reason it is deferred

---

## Dependencies at a glance

```
Phase 0  seams frozen ───────────────┐
Phase 1  A: pages/heap/pool          │ parallel
         B: parser, noise            │ (build against seams)
         C: attack lab v1 (Django)   │
Phase 2  A: B+tree, catalog, loader  │ parallel
         B: planner, DP ops, ledger  │
         C: gateway(stub), frontend  │
Phase 3  integrate: executor + DP ops + rewriter + pgwire + UI   ← sync
Phase 4  benchmarks · proptest · differential · attack lab v2
Phase 5  bug bash · cutover · report
```

If a lane falls behind, Phase 3 slips for everyone, so at each gate cut scope
(e.g. skip `Monaco`, keep CodeMirror) before extending the schedule. Cut order:
4.7 plan viewer → 4.6 Monaco → 4.2 encryption → 3.6 overlap detection.
