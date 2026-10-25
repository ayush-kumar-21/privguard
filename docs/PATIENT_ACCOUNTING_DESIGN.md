# Patient-centric privacy accounting: design

**Status:** proposed, not built. **Where it lives:** the graded Django + PostgreSQL + React application
(`PrivGuard_Rubric_Alignment.md` section 0). The Rust engine stays the research appendix and is not touched.

## 1. The idea in one paragraph

Today the privacy budget belongs to the *analyst* (`sys_budget_account`). Three real problems follow:
several analysts can pool results, so a patient's total loss is the sum over everyone, not what any one
account was allowed (collusion, and `/api/auth/register/` makes accounts cheap); a patient cannot check
what has been spent on them; and deleting a patient has no defined effect on the accounting. This design
adds a second ledger whose unit is the **patient** (the privacy unit, `patients.patient_id`), keeps it
inside the same trigger / stored-procedure / transaction machinery as the analyst ledger, and lets an
auditor produce a **receipt** for any patient that can be checked without trusting the application.

## 2. How it fits the rubric (no new frameworks, nothing cut)

| Rubric element | How the new work uses it |
|---|---|
| ER design, 3NF, every table in an ER figure | two new entities and their relationships, added to the ER document and data dictionary before any migration is merged |
| Stored procedure | `sp_reserve_budget` / `sp_settle_budget` extended; new `sp_erase_patient` |
| Trigger | new hash-chain trigger and append-only trigger on the patient ledger, same pattern as `trg_ledger_hash` and `trg_ledger_readonly` |
| Views | new `v_patient_budget`; `v_budget_status` unchanged |
| Transactions | the per-patient charge happens inside the existing reserve / execute / settle transaction, so it commits or aborts with the analyst charge |
| ORM-only data access | patients to charge are found with the same ORM queryset that already does contribution bounding |
| RBAC | `auditor` and `dba` read receipts and per-patient budgets; analysts see neither |
| "Never cut" core | extends contribution bounding, the ledger with trigger and transaction, and the DP service; memoisation is unchanged |
| Declared scope | one privacy unit; malicious-DBA is out of scope, so receipts are **tamper-evident, not tamper-proof** |

## 3. Feature 1: per-patient budget

**Schema (new ER entities).**
- `sys_patient_budget(patient_id PK/FK, db_id FK, rho_total, rho_spent, rho_reserved, status)`.
  One row per patient. CHECK constraints mirror `sys_budget_account` (`rho_spent <= rho_total - rho_reserved`).
  The allowance comes from a database-level default (`sys_database.default_patient_rho`).
- `sys_patient_ledger(entry_id, patient_id FK, query_id FK, op, rho_delta, rho_after, prev_hash, entry_hash)`.
  Append-only and hash-chained **per patient**.

**Charging rule.** For query `q` at cost `rho_q` (the same `rho_q` the analyst is charged):
1. Build the set `U_q` of patients with at least one row that survives the filters and the contribution
   bound `C` (the queryset the service already builds).
2. A patient in `U_q` is charged `rho_q`. A patient not in `U_q` is charged nothing, because removing them
   cannot change the output.
3. A patient whose remaining budget is below `rho_q` is **excluded before the query runs** (their rows
   are treated as absent) and is not charged. The analyst is charged as usual.

**Protocol.** `sp_reserve_budget` reserves analyst and patient budgets in one transaction (`SELECT ... FOR
UPDATE` on the patient rows, ordered by id to avoid deadlock); `sp_settle_budget` commits or aborts both.
An abort refunds both, exactly as the analyst budget works today.

**Soundness, and what it depends on.** Charging only the patients who actually contribute, and excluding
exhausted ones, is the individual-accounting result of Feldman and Zrnic (NeurIPS 2021, Renyi filter).
Our zCDP charge `rho` implies `(alpha, alpha*rho)` Renyi DP, which is the form their filter takes. Before
the report claims this, read their theorem and confirm its conditions (adaptive queries, the exclusion
rule) match this design; the tests in section 7 are a second line of defence, not a proof.

**Open question with a safe default.** Releasing "some patients were excluded" would itself depend on
data. Default: analysts get no exclusion information; auditors and the DBA see per-patient budgets.

**Why this answers collusion.** A patient's total spend is capped no matter how many analyst accounts
asked. Opening a second account no longer buys a second budget.

## 4. Feature 2: patient privacy receipts

`fn_verify_patient_chain(patient_id)` replays that patient's chain (same idea as `fn_verify_ledger`).
`GET /api/patients/<id>/privacy-receipt/` (auditor or dba) returns the patient's entries, head hash and
total spent. A standalone `tools/verify_receipt.py` (standard library only) recomputes every hash from the
JSON, so a regulator can check a receipt without the application or the database.

Claim, stated carefully: the receipt lets a third party confirm that *the ledger they were given is
internally consistent and append-only*. It cannot prove the operator did not keep a second ledger
(malicious DBA: out of scope).

## 5. Feature 3: erasure

`sp_erase_patient(patient_id, actor)`, one transaction: write an `erased` entry to the patient's chain, set
`status = 'erased'`, then remove or anonymise the patient's rows through the ORM. After that the patient is
never in `U_q`. **Past spend is not refunded** and past answers stay valid (they are noisy releases). The
procedure returns an erasure certificate. No legal claim is made; the point is a defined, auditable rule
for a question the literature leaves open.

## 6. Feature 4: self-audit

An automated check that the mechanisms behave as claimed: draw many outputs on two neighbouring datasets,
estimate the privacy loss with a hypothesis test (StatDP / DP-Sniper style), and fail the build if it
exceeds the claimed epsilon. Shown on the Attack Lab page with error bars over at least 30 trials, as the
rubric's evaluation checklist asks. Counts as the "automated testing" best-practice bonus.

## 7. Tests and evaluation

- Unit: charge only contributors; exhausted patient excluded and not charged; abort refunds both ledgers;
  chain tamper detected; erased patient never charged.
- Concurrency: parallel queries cannot overspend one patient (as the quota test does for analysts).
- Collusion demo for the report: N analyst accounts ask the same question; show total patient loss capped.
- Cost: query latency before and after (patient rows locked per query), with error bars.
- Docs updated in the same change: ER figure, data dictionary, README, monitor Database page.

## 8. Related work (what is and is not new)

| Work | Overlap |
|---|---|
| Sage (SOSP 2019) | budgets per data block, including per-user blocks; an ML platform, not a relational app |
| Feldman and Zrnic (NeurIPS 2021) | the individual-accounting theory this design relies on |
| DProvDB (SIGMOD 2024) | per-analyst provenance; the unit is the analyst |
| VAMS; WFA budget manager | append-only audit ledgers owned by the operator |
| CacheDP, ReBound | cached-answer reuse: already published, so **not** proposed here |

The contribution claimed: per-patient budgets and verifiable per-patient receipts inside a relational
application, using database triggers and stored procedures, with a defined erasure rule. The combination
is what is new; each part has precedent. Run one more literature search before putting a novelty score in
the report.

## 9. Order and size

1. Feature 1 (large): schema, two migrations, service change, tests, ER and dictionary updates.
2. Feature 2 (medium): function, endpoint, verifier script, tests.
3. Feature 3 (small to medium): one procedure, one endpoint, tests.
4. Feature 4 (small): test module and an Attack Lab panel.

Cut order if time runs short: feature 4, then 3, then 2. Never cut 1.
