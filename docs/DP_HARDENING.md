# Attack-driven hardening of the DP gateway

PrivGuard's claim is that **privacy is enforced by the system, not trusted to the analyst**. That
only holds if the analyst has no path around the system. This document records the places we found
where one existed, the published attack class each corresponds to, the fix, and the regression test
that keeps it fixed. Everything below is covered by `backend/tests/test_dp_hardening.py`.

## Findings and fixes

| # | Finding in our implementation | Published attack class | Fix | Regression test |
|---|---|---|---|---|
| 1 | `GET /api/queries/` returned `rows_scanned` — for a COUNT, the **exact un-noised answer** — to the analyst who ran the query (reproduced: DP answer 15 vs `rows_scanned` 40 = true count). Complete budget-free bypass. | Output channels of DP systems (Haeberlen, Pierce, Narayan: *Differential Privacy Under Fire*, USENIX Sec 2011) | Field removed from the API for everyone. `latency_ms` only for dba/auditor; analysts cannot sort by it. | `TestOutputChannels` |
| 2 | Noise sampled with floating-point inverse-CDF (Laplace) and Box-Muller (Gaussian). | Mironov, CCS 2012; Jin et al., IEEE S&P 2022 (Gaussian) | Exact integer samplers (Canonne–Kamath–Steinke, NeurIPS 2020) on an integer grid; every parameter rounded in the conservative direction. | `TestExactSamplers` |
| 3 | Sampler running time is observable. | Jin et al., IEEE S&P 2022 (timing) | Response-time floor `PRIVGUARD_MIN_RESPONSE_MS` (default 150 ms). | (config; sampler tests) |
| 4 | DP partition selection ignored the L0 bound G: a unit in G groups influences G releases, but each was noised as if it were one. Understated that stage's cost by a factor of G. δ was also an untracked 1e-2. | Wilson et al., *Differentially Private SQL with Bounded User Contribution*, PoPETs 2020 (Thm 2: ε/C_u per count) | `partition_params`: ε0 = √(2ρ_part/G), δ_p = 1−(1−δ)^(1/G), threshold from the exact discrete-Laplace tail. δ now recorded per query (`sys_query.delta_charged`) and summed per account (`delta_spent`). | `TestPartitionSelection` (incl. an empirical release-probability check) |
| 5 | No database-wide limit: K analysts with budgets get independent noise for the same query and can average it away. | Collusion / Sybil weakness of per-user budgets (e.g. Big Bird, arXiv 2506.05290, in an adjacent setting) | `SysDatabase.rho_global_cap`, enforced **inside** `sp_reserve_budget` under a row lock on the database. | `TestGlobalBudgetCap` |
| 6 | `GROUP BY` returned one group per *row* (n=1, units=1), so every group was suppressed and grouped queries always returned nothing. (Found while verifying the above; unrelated to the research.) | — | Aggregate over the contribution-bounded rows via a subquery. | `TestGroupedAggregate` |

## What this does and does not claim

* **Verified by running it:** finding 1 (reproduced end to end on the seeded database, fixed, re-run), finding 6, the global cap through the API, and the statistical behaviour of the samplers.
* **Established by analysis, not by an exploit:** findings 2–4. We did not build a working floating-point or timing attack against the old code; the old code used the constructions those papers show to be unsafe in general. The old `round(value, 4)` post-processing is not Mironov's snapping mechanism.
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
