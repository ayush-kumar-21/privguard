# Algorithm upgrades

Same pipeline (clamp, contribution bound, DP aggregate, partition select, ledger). These change what
the same privacy cost buys. Measurements: `docs/eval/algorithms.md` (`tools/eval_algorithms.py`).

## Done

**1. Tighter budget-to-epsilon conversion** (`backend/privacy/accounting.py`, mirrored in
`frontend/src/lib/accounting.ts`). The ledger stores rho; epsilon is only the human-readable
equivalent. For every Renyi order a > 1 a rho-zCDP mechanism is (eps, delta)-DP with
`eps_a = a*rho - (ln delta + ln(a-1) - a*ln(1-1/a)) / (a-1)` (Canonne, Kamath and Steinke 2020, Prop. 1.3).
Every `a` is valid, so the search over `a` affects tightness only, and the result is capped by the
classic bound. Result: reported epsilon 4-14% lower; the noise and the ledger are untouched.
Validity is tested against the exact epsilon of a Gaussian mechanism with the same rho, which no valid
conversion may go below (24 rho/delta pairs).

**2. Centred mean for AVG** (`_execute_scalar`). Noise the sum of `(x - c)`, `c` = midpoint of the
clamp interval, instead of the sum of `x`. One patient changes it by at most `C*(hi-lo)/2` instead of
`C*max(|lo|,|hi|)`, so the same rho gives up to M/h times less noise (2x for a `[0, hi]` clamp). The
count is noised separately; `c + S'/N'` is post-processing. Result: AVG RMSE 47-56% lower on the golden
data. SUM and COUNT are unchanged. The ledger records the new, smaller sensitivity for AVG.

Both apply to the Django application. The Python oracle and the Rust engine keep the old formulas and
their golden values, so they do not match the Django AVG; the oracle and engine are the research
appendix and are not touched here.

## Candidates, not built

| Algorithm | Component | Source | Expected gain | Cost |
|---|---|---|---|---|
| Optimal partition selection | GROUP BY | Desfontaines et al., PoPETs 2022; DP-SIPS, PoPETs 2023 | more groups released at the same delta (proved optimal when each patient is in one group; ours allows G groups, so measure first) | medium |
| Private choice of clamp bounds and C | contribution bounding | Desfontaines thesis (approximate bounds); Wilson et al. 2019 | removes the bias or noise caused by a badly set policy; the spend would be its own ledger operation | medium to large |
| Workload-optimal batches | several GROUP BYs at once | HDMM; ResidualPlanner (NeurIPS 2023) | lower error for related queries | large, and changes the interface |
