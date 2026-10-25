# What the algorithm upgrades buy

Regenerate with `python3 tools/eval_algorithms.py --write`.

## 1. Tighter budget-to-epsilon conversion (reporting only; the noise is unchanged)

| rho | delta | classic eps | tighter eps | exact-Gaussian floor | lower by |
|---:|---:|---:|---:|---:|---:|
| 0.05 | 1e-06 | 1.712 | 1.472 | 1.368 | 14.1% |
| 0.5 | 1e-06 | 5.757 | 5.222 | 4.887 | 9.3% |
| 5.0 | 1e-06 | 21.623 | 20.552 | 19.424 | 5.0% |
| 5.0 | 1e-09 | 25.358 | 24.406 | 23.967 | 3.8% |

The floor is the exact epsilon of a Gaussian mechanism with that rho; no valid conversion can go below it.

## 2. Centred mean for AVG (same privacy cost, less noise)

Golden dataset, 20,000 noise draws per row. RMSE of AVG(total_charge); the last column is the standard error of the RMSE.

| clamp | C | rho | true mean | plain-sum RMSE | centred RMSE | lower by | s.e. |
|---|---:|---:|---:|---:|---:|---:|---:|
| [0, 1000] | 5 | 0.5 | 664.1 | 59.0 | 25.9 | 56% | 0.20 |
| [0, 1000] | 5 | 2.0 | 664.1 | 29.3 | 12.9 | 56% | 0.10 |
| [-50, 400] | 2 | 0.5 | 342.7 | 10.3 | 5.5 | 47% | 0.04 |
