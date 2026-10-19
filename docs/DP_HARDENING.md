# Attack-driven hardening of the DP gateway

PrivGuard's claim is that **privacy is enforced by the system, not trusted to the analyst**. That
only holds if the analyst has no path around the system. This document records the places we found
where one existed, the published attack class each corresponds to, the fix, and the regression test
that keeps it fixed. Everything below is covered by `backend/tests/test_dp_hardening.py`, and
the effect of each fix is measured in [`EVALUATION.md`](EVALUATION.md).

## Findings and fixes

| # | Finding in our implementation | Published attack class | Fix | Regression test |
|---|---|---|---|---|
| 1 | `GET /api/queries/` returned `rows_scanned` — for a COUNT, the **exact un-noised answer** — to the analyst who ran the query (reproduced: DP answer 15 vs `rows_scanned` 40 = true count). Complete budget-free bypass. | Output channels of DP systems (Haeberlen, Pierce, Narayan: *Differential Privacy Under Fire*, USENIX Sec 2011) | Field removed from the API for everyone. `latency_ms` only for dba/auditor; analysts cannot sort by it. | `TestOutputChannels` |
| 2 | Noise sampled with floating-point inverse-CDF (Laplace) and Box-Muller (Gaussian). | Mironov, CCS 2012; Jin et al., IEEE S&P 2022 (Gaussian) | Exact integer samplers (Canonne–Kamath–Steinke, NeurIPS 2020) on an integer grid; every parameter rounded in the conservative direction. | `TestExactSamplers` |
| 3 | Sampler running time is observable. | Jin et al., IEEE S&P 2022 (timing) | Response-time floor `PRIVGUARD_MIN_RESPONSE_MS` (default 150 ms). | (config; sampler tests) |
| 4 | DP partition selection ignored the L0 bound G: a unit in G groups influences G releases, but each was noised as if it were one. Understated that stage's cost by a factor of G. δ was also an untracked 1e-2. | Wilson et al., *Differentially Private SQL with Bounded User Contribution*, PoPETs 2020 (Thm 2: ε/C_u per count) | `partition_params`: ε0 = √(2ρ_part/G), δ_p = 1−(1−δ)^(1/G), threshold from the exact discrete-Laplace tail. δ now recorded per query (`sys_query.delta_charged`) and summed per account (`delta_spent`). | `TestPartitionSelection` (incl. an empirical release-probability check) |
| 5 | No database-wide limit: K analysts with budgets get independent noise for the same query and can average it away. | Collusion / Sybil weakness of per-user budgets (e.g. Big Bird, arXiv 2506.05290, in an adjacent setting) | `SysDatabase.rho_global_cap`, enforced **inside** `sp_reserve_budget` under a row lock on the database. | `TestGlobalBudgetCap` |
| 6 | `GROUP BY` returned one group per *row* (n=1, units=1), so every group was suppressed and grouped queries always returned nothing. (Found while verifying the above; unrelated to the research.) | — | Aggregate over the contribution-bounded rows via a subquery. | `TestGroupedAggregate` |
| 7 | The **raw noise seed** was stored in `sys_noise_cache` (and so in every `pg_dump`). A seed regenerates the noise exactly: answer − noise = true count. Reproduced from the table alone (Exp 4B: recovered 40 = ground truth). | Same replay idea as the sampler attacks, applied to storage; the data-minimisation principle | Store a one-way SHA-256 *commitment* instead; a data migration overwrites existing rows (irreversible by design). | `TestNoiseSeedIsNeverStored` |
| 8 | The exact count was persisted (`sys_query.rows_scanned`) and exposed by the `v_query_audit` view and backups, even after the API stopped returning it. | Data minimisation | Column removed, view rebuilt without it. | `test_no_exact_count_is_persisted_or_returned` |
| 9 | No database-level boundary for analyst-facing access: anything holding analyst credentials could read raw rows, noise material, other analysts' history. | Defence in depth | Role `privguard_analyst`: no privilege on raw tables, noise cache, ledger, principals or the exact-aggregate views; explicit column grants on `sys_query` / `sys_budget_account`; row-level security keyed on the authenticated login `session_user` (`analyst_<id>`). | `TestRawDataIsUnreachable`, `TestRowLevelSecurity`, Exp 4A |
| 10 | `PRIVGUARD_SERVER_SECRET` was never set by `render.yaml` or `docker-compose.yml`, so a deployment ran with the **public default** `dev-noise-secret`. The seed is derived from that secret plus the query hash plus the caller's id, all of which an analyst knows, so they could replay and subtract their own noise (the Exp 4B attack, with the seed derived instead of read). | Key management | `render.yaml` generates it; the app **refuses to start** with DEBUG off if `SECRET_KEY` or the noise secret is a known placeholder. | `TestSecretGuard` (and verified: the app exits with `ImproperlyConfigured`) |

## What this does and does not claim

* **Verified by running it:** finding 1 (reproduced end to end on the seeded database, fixed, re-run), finding 6, the global cap through the API, and the statistical behaviour of the samplers.
* **Established by analysis, not by an exploit:** findings 2–4. We did not build a working floating-point or timing attack against the old code; the old code used the constructions those papers show to be unsafe in general. The old `round(value, 4)` post-processing is not Mironov's snapping mechanism.
* **Design lesson (finding 9).** The first draft of the row-level policy keyed on a session variable (`app.principal_id`). Caught in review before it shipped: a client with a raw SQL session could simply `SET` it to someone else's id. The policy now keys on `session_user`, which a non-superuser cannot change, and `test_setting_a_session_variable_does_not_help` pins that.
* **What the database role does not do.** The gateway connects as the table owner, which PostgreSQL exempts from row-level security, so the application is unaffected — and so is anyone holding *those* credentials. The role protects analyst-facing connections only. Analyst logins (`CREATE ROLE analyst_<id> LOGIN … IN ROLE privguard_analyst`) must be provisioned by an operator; nothing in the app does it yet. On a managed database that forbids `CREATE ROLE`, the migration skips the role (and the tests skip) rather than failing the deploy. We could not test inside one transaction that a real analyst cannot `SET SESSION AUTHORIZATION` to another login, because PostgreSQL only forbids it when the *original* login was not a superuser.
* **Accounting is approximate (ρ, δ).** The ε part of partition selection is charged to the ρ ledger; its δ is tracked separately and not enforced against a cap.
* **Utility cost.** Correct partition selection needs a higher threshold, so small groups are suppressed more often at low ρ. That is the honest price of the guarantee.
* **Not addressed:** non-timing side channels of the database itself, a malicious DBA, and cross-privacy-unit joins (out of scope in the proposal).

## Sources

* Mironov, *On Significance of the Least Significant Bits for Differential Privacy*, CCS 2012.
* Jin, McMurtry, Rubinstein, Ohrimenko, *Are We There Yet? Timing and Floating-Point Attacks on Differential Privacy Systems*, IEEE S&P 2022 — https://arxiv.org/abs/2112.05307
* Casacuberta, Shoemate, Vadhan, Wagaman, *Widespread Underestimation of Sensitivity in Differentially Private Libraries and How to Fix It*, CCS 2022 — https://arxiv.org/abs/2207.10635
* Canonne, Kamath, Steinke, *The Discrete Gaussian for Differential Privacy*, NeurIPS 2020 — https://arxiv.org/abs/2004.00010
* Wilson et al., *Differentially Private SQL with Bounded User Contribution*, PoPETs 2020 — https://arxiv.org/abs/1909.01917
* Haeberlen, Pierce, Narayan, *Differential Privacy Under Fire*, USENIX Security 2011 — https://haeberlen.cis.upenn.edu/papers/fuzz-sec2011.pdf
* Gadotti et al., *When the Signal is in the Noise: Exploiting Diffix's Sticky Noise*, USENIX Security 2019 — https://arxiv.org/abs/1804.06752 (why our memoisation charges every syntactic variant of a query instead of reusing noise)
