# Phase 0 kickoff prompt

Paste into Claude Code (or any coding agent) from the repo root. Replace `<LANE>` with
`A`, `B` or `C` and `<NAME>` with your name. Lane definitions: [`TEAM_PLAN.md`](TEAM_PLAN.md).

---

```
You are helping me, <NAME>, on PrivGuard: a differentially private query engine
(Rust engine + FastAPI gateway + React UI; a Django stack still runs as the legacy demo).
I am on Lane <LANE> of a 3-person team.

Read first, in this order, before touching anything:
1. TEAM_PLAN.md   — lanes, phases, rules. We are starting Phase 0.
2. IMPLEMENTATION_PLAN.md — task ids and checkboxes.
3. SPEC.md        — frozen contract. Do not edit it except via the Amendments section.

Then do ONLY my lane's Phase 0 tasks from TEAM_PLAN.md:
- Lane A: define the RowSource trait + Value/Row types in privguard-core and the PageStore
  trait in privguard-storage, each with an in-memory implementation and a unit test.
- Lane B: make the oracle test suite run reproducibly and confirm the count against the
  README; write the golden-value fixtures (20 queries x fixed seed -> expected noisy
  answers, as committed JSON); define PrivacyPolicy and QueryFingerprint types.
- Lane C: fix bugs P0-1 .. P0-6 in the TEAM_PLAN.md bug table; add the attacks/ skeleton;
  make CI run backend, oracle and frontend type-check tests plus `npm audit --audit-level=high`.

Rules:
- Work on a new branch named phase0/<lane>-<short-topic> off the latest origin/main.
  Never push to main. One PR per task, small and reviewable.
- Every bug fix includes a regression test in the same PR. No test, no merge.
- Before claiming a task is done, run the relevant checks and show me the output:
  cargo fmt --check, cargo clippy -D warnings, cargo test, the oracle tests, backend tests,
  and the frontend type-check, whichever apply to what you changed.
- Do not touch files owned by other lanes. If a seam (trait/type) needs to change, stop and
  tell me so I can agree it with the other lane.
- Prefer the smallest change that works. No speculative abstractions.
- When finished, tick the boxes in IMPLEMENTATION_PLAN.md, update its Resume point, and
  give me a short summary: what changed, what you verified, what is left.

Start by listing the Phase 0 tasks for Lane <LANE>, and the order you will do them in.
Wait for my OK before you start editing.
```

---

## Running lanes at the same time

Everyone pushes to GitHub independently; there is no conflict as long as each person uses
their own branch (`phase0/a-...`, `phase0/b-...`, `phase0/c-...`) and only edits their
lane's files. To run several agent sessions on one machine, give each its own worktree:

```
git worktree add ../privguard-lane-a -b phase0/a-seams origin/main
git worktree add ../privguard-lane-b -b phase0/b-fixtures origin/main
git worktree add ../privguard-lane-c -b phase0/c-bugfixes origin/main
```

Shared files (`IMPLEMENTATION_PLAN.md`, CI config) are the only conflict risk: edit them in
small commits and rebase on `origin/main` before opening the PR.
