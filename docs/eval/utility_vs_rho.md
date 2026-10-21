# Utility vs privacy budget

Mean error of the oracle's answers over 200 independent noise seeds, against the bounded (clamped, contribution-limited) answer. Dataset: the 211-visit golden set, C=5, G=3. Regenerate with `cd oracle && uv run python tools_utility.py`.

| query | rho | mean abs error | mean rel error |
|---|---:|---:|---:|
| COUNT(*) | 0.01 | 34.95 | 17.83% |
| COUNT(*) | 0.02 | 24.71 | 12.61% |
| COUNT(*) | 0.05 | 15.62 | 7.97% |
| COUNT(*) | 0.1 | 11.02 | 5.62% |
| COUNT(*) | 0.25 | 7.00 | 3.57% |
| COUNT(*) | 0.5 | 4.88 | 2.49% |
| COUNT(*) | 1.0 | 3.48 | 1.78% |
| COUNT(*) | 2.0 | 2.44 | 1.25% |
| COUNT(*) | 4.0 | 1.74 | 0.89% |
| SUM(total_charge) | 0.01 | 33,801.24 | 27.01% |
| SUM(total_charge) | 0.02 | 23,901.09 | 19.10% |
| SUM(total_charge) | 0.05 | 15,116.38 | 12.08% |
| SUM(total_charge) | 0.1 | 10,688.90 | 8.54% |
| SUM(total_charge) | 0.25 | 6,760.25 | 5.40% |
| SUM(total_charge) | 0.5 | 4,780.22 | 3.82% |
| SUM(total_charge) | 1.0 | 3,380.13 | 2.70% |
| SUM(total_charge) | 2.0 | 2,390.11 | 1.91% |
| SUM(total_charge) | 4.0 | 1,690.07 | 1.35% |
| AVG(total_charge) | 0.01 | 2,298.48 | 360.04% |
| AVG(total_charge) | 0.02 | 337.65 | 52.89% |
| AVG(total_charge) | 0.05 | 160.90 | 25.20% |
| AVG(total_charge) | 0.1 | 110.20 | 17.26% |
| AVG(total_charge) | 0.25 | 68.58 | 10.74% |
| AVG(total_charge) | 0.5 | 48.25 | 7.56% |
| AVG(total_charge) | 1.0 | 34.03 | 5.33% |
| AVG(total_charge) | 2.0 | 24.03 | 3.76% |
| AVG(total_charge) | 4.0 | 16.98 | 2.66% |
