# Per-person prompts (phase allocation)

Allocation: **Sugeshkumar → Phases 0–1 · Gourishankar → Phases 2–3 · Ayush → Phases 4–5.**
Phase contents are in [`TEAM_PLAN.md`](TEAM_PLAN.md). Each person pastes their block into
Claude Code from the repo root.

Because phases depend on each other, work is handed over at a **gate**: the previous owner
merges to `main`, ticks `IMPLEMENTATION_PLAN.md`, updates its Resume point, and tells the
next owner. Until then the next owner does the "while you wait" prep in their prompt.

The old "only touch your own lane's files" rule no longer applies: a phase owner does all
three lanes' tasks for their phases (the A/B/C rows in each phase table).

---

## Sugeshkumar — Phases 0 and 1

```
You are helping me, Sugeshkumar, on PrivGuard: a differentially private query engine
(Rust engine + FastAPI gateway + React UI; a Django stack still runs as the legacy demo).
Team: Gourishankar owns Phases 2-3 after me, Ayush owns Phases 4-5. I own Phases 0 and 1.

Setup: repo https://github.com/ayush-kumar-21/privguard. Run `git fetch origin`. If
TEAM_PLAN.md is not on origin/main yet, base on origin/docs/team-plan.

Read first, in order: TEAM_PLAN.md (Phases 0 and 1, and the Known bugs table),
IMPLEMENTATION_PLAN.md (task ids), SPEC.md (frozen contract; change only via Amendments).

PHASE 0 — do ALL the rows (A, B and C) of the Phase 0 table:
- Rust seams: `RowSource` trait + `Value`/`Row` types in privguard-core; `PageStore` trait with
  an in-memory impl in privguard-storage. Unit-test each. Build on the existing compile-time
  layout assertions in privguard-storage/src/lib.rs.
- Oracle: make the test suite run reproducibly (uv), report the real passing count, and fix the
  README if it differs from 93. Write 20 golden queries x fixed seed -> expected noisy answers as
  language-neutral JSON (oracle/tests/golden/), covering COUNT/SUM/AVG, GROUP BY with partition
  selection and all three mechanisms; add a test that fails if the answers change.
  Define `PrivacyPolicy` and `QueryFingerprint` types in privguard-privacy (SPEC 4.6).
- Bugs P0-1..P0-6 from the TEAM_PLAN table: npm audit fix; refuse to start with default
  SECRET_KEY when DEBUG=False and read compose secrets from a git-ignored .env with a committed
  .env.example; CheckConstraint check= -> condition= without changing migration state
  (verify with `makemigrations --check`); bind DB/API ports to 127.0.0.1; verify the nginx/CORS
  situation and fix only if broken; oracle tests in CI.
- `attacks/` skeleton (README + demo.py stubs) and CI running backend, oracle, frontend type-check
  and `npm audit --audit-level=high`.

PHASE 1:
- Storage: 2.1 8 KiB slotted page (SPEC 5.1, CRC32C), 2.2 heap file with scan, 2.3 CLOCK buffer
  pool. Tests: RID round-trip, eviction with a pool smaller than the file, CRC catches a flipped byte.
- SQL: 2.6 parser & binder over sqlparser-rs against a MOCK catalog (SPEC 3 subset; unsupported
  SQL returns the SPEC 9 error). 3.1 verify Rust zCDP vs the oracle. 3.2 noise mechanisms with a
  keyed deterministic stream, matching oracle golden values bit-for-bit.
- Attack lab v1: attacks/demo.py implements the differencing attack and the averaging attack
  against raw Postgres and against the API on :55001, and prints a comparison table. Any hole
  it exposes is logged as a bug in IMPLEMENTATION_PLAN.md.

Rules:
- One branch per task: phase0/<topic>, phase1/<topic>. Never push to main. One small PR each;
  ask a teammate to review. Every bug fix includes a regression test.
- Never commit real secrets. No new dependencies unless unavoidable; say why if you add one.
- Prefer the smallest change that works.
- Before saying a task is done, show the output of what applies: oracle tests, cargo fmt --check,
  cargo clippy -D warnings, cargo test (`./run.sh engine`), backend tests, frontend type-check.
- GATE: when Phase 1 is merged, tick the boxes, update the Resume point in IMPLEMENTATION_PLAN.md
  and tell Gourishankar he can start Phase 2. Do the Phase 0 seams first and tell him as soon as
  they merge, because his Phase 2 builds on them.

Start by listing your tasks in order. Wait for my OK before editing.
```

---

## Gourishankar — Phases 2 and 3

```
You are helping me, Gourishankar, on PrivGuard: a differentially private query engine
(Rust engine + FastAPI gateway + React UI; a Django stack still runs as the legacy demo).
Team: Sugeshkumar owns Phases 0-1 before me, Ayush owns Phases 4-5 after me. I own Phases 2 and 3.

Setup: repo https://github.com/ayush-kumar-21/privguard. Run `git fetch origin`.

Read first, in order: TEAM_PLAN.md (Phases 2 and 3), IMPLEMENTATION_PLAN.md (task ids),
SPEC.md (frozen contract; change only via the Amendments section).

WHILE YOU WAIT for Sugeshkumar's Phase 0 seams and Phase 1 storage to merge, do prep that needs
neither: read SPEC sections 4-7 and 8, and write a short design note (docs/phase2-design.md) for
the B+ tree node layout, catalog tables, ledger entry format and executor operator interfaces.
Do not write engine code against traits that are not merged yet. Start Phase 2 when he says go.

PHASE 2 — do ALL the rows of the Phase 2 table:
- Engine: 2.4 B+ tree (point lookup, range scan, split/merge), 2.5 self-hosted catalog persisted
  in the store, 2.9 loader (synthetic dataset -> heap files).
- SQL/privacy: 2.7 logical planner; 3.3 DP operators Clamp -> ContributionBound -> DPAggregate ->
  PartitionSelect over the `RowSource` trait (tested on in-memory rows against the oracle golden
  fixtures); 3.4 budget ledger (blake3 chain, reserve -> commit/abort; tampering is detected).
- Interfaces: 4.4 FastAPI gateway scaffold (JWT with python-jose, argon2) talking to a STUB engine
  returning canned results; 4.8 repoint the frontend API client at the gateway.

PHASE 3 — integration; one query end to end:
- 2.8 Volcano executor (Scan, Filter, Aggregate, GroupBy) over heap + index; 2.10 baseline
  correctness vs the oracle's non-private aggregates; 4.2 AES-256-GCM column encryption.
- Plug the DP operators into real executor plans; 3.5 rewriter: fingerprint -> policy check ->
  noise cache; 3.6 predicate-overlap detection; 4.1 RBAC in the binder.
- 4.3 pgwire v3 server; 4.5 gateway -> engine client path; 4.6 Monaco console; 4.7 react-flow plan
  viewer; 4.9 policy grid.
- Gate demo: `SELECT COUNT(*)` from the UI hits the real engine, is charged to the ledger, and a
  repeat returns the cached answer at zero cost.

This is the biggest block of work in the project. If it slips, cut scope in this order and tell
me before cutting: 4.7 plan viewer -> 4.6 Monaco -> 4.2 encryption -> 3.6 overlap detection.

Rules:
- One branch per task: phase2/<topic>, phase3/<topic>. Never push to main. Small PRs; ask a
  teammate to review. Update the oracle golden fixtures only with the reviewer's agreement.
- No new Rust dependencies beyond what SPEC lists (sqlparser-rs, pgwire, tokio, blake3, aes-gcm),
  and add each one only in the task that needs it.
- Prefer the smallest change that works. No speculative abstractions.
- Before saying a task is done, show the output of what applies: cargo fmt --check,
  cargo clippy -D warnings, cargo test (`./run.sh engine`), oracle tests, gateway tests, frontend
  type-check.
- GATE: when Phase 3's demo works and is merged, tick the boxes, update the Resume point and
  tell Ayush he can start Phase 4.

Start by listing your tasks in order. Wait for my OK before editing.
```

---

## Ayush — Phases 4 and 5

```
You are helping me, Ayush, on PrivGuard: a differentially private query engine
(Rust engine + FastAPI gateway + React UI; a Django stack still runs as the legacy demo).
Team: Sugeshkumar owns Phases 0-1, Gourishankar owns Phases 2-3 before me. I own Phases 4 and 5.

Setup: repo https://github.com/ayush-kumar-21/privguard. Run `git fetch origin`.

Read first, in order: TEAM_PLAN.md (Phases 4 and 5 and the bug-bash exit checklist),
IMPLEMENTATION_PLAN.md (task ids), SPEC.md (frozen contract; change only via Amendments).

WHILE YOU WAIT for Gourishankar's Phase 3 to merge, do prep that does not need the engine:
- Review each merged PR from Phases 0-3 (use /code-review) and log findings in
  IMPLEMENTATION_PLAN.md. This is the earliest bug-finding you can do.
- Draft the attack list for Attack lab v2: differencing, averaging, budget exhaustion, ledger
  tampering, SQL injection through the gateway, JWT forgery, and a column-policy bypass. For each,
  write the expected defence, so each is ready to become a test.
- Plan the proptest properties and fuzz targets (page decoder, wire parser, SQL parser).
Do not write engine code until Phase 3 is merged.

PHASE 4 — do ALL the rows of the Phase 4 table:
- pgwire hardening; 5.1 criterion benchmarks (DP overhead vs the non-private baseline); fuzz the
  page decoder and wire parser.
- 3.7 proptest: budget never over-spends, ledger never forks, noise cache never leaks fresh noise.
  3.8 differential test: engine vs oracle on identical seeds, 1000+ random queries.
- 5.3 attack lab v2 against the new engine (every attack in your list; each one a CI test);
  5.2 error-vs-rho utility curves.

PHASE 5 — bug bash and ship:
- Fix everything the fuzzers, proptests and differential test found. Crash and torn-write
  behaviour documented and tested; long-run leak check.
- Re-verify the sensitivity bounds for each aggregate by hand; record any SPEC divergence in
  SPEC section 10 Amendments.
- Run /code-review and /security-review across the whole repo; full E2E through the UI.
- 5.5 cutover: compose runs engine + gateway + ui, delete backend/. 5.4 report, 5.6 demo polish.
- Phase 5 is done only when every box in the TEAM_PLAN bug-bash exit checklist is ticked.

Rules:
- One branch per task: phase4/<topic>, phase5/<topic>. Never push to main. Small PRs; ask a
  teammate to review. Every bug fix includes a regression test.
- Report outcomes faithfully: if a test, attack or checklist item fails, say so with the output.
  Never loosen a test to make it pass.
- Prefer the smallest change that works.
- Before saying a task is done, show the output of what applies: cargo fmt --check,
  cargo clippy -D warnings, cargo test, oracle tests, gateway tests, frontend type-check,
  cargo audit, npm audit, pip-audit.
- FINISH: tick the final checklist, update the README status table and test counts so they match
  reality, and give me a summary with anything deferred and why.

Start by listing your tasks in order. Wait for my OK before editing.
```
