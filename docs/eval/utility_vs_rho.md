# Utility vs privacy budget

Mean error of the oracle's answers over 200 independent noise seeds, against the bounded (clamped, contribution-limited) answer. Dataset: the 211-visit golden set, C=5, G=3. Regenerate with `cd oracle && uv run python tools_utility.py`.

| query | rho | mean abs error | mean rel error |
|---|---:|---:|---:|
| COUNT(*) | 0.01 | 34.55 | 17.63% |
| COUNT(*) | 0.02 | 26.83 | 13.69% |
| COUNT(*) | 0.05 | 16.75 | 8.55% |
| COUNT(*) | 0.1 | 11.82 | 6.03% |
| COUNT(*) | 0.25 | 7.38 | 3.77% |
| COUNT(*) | 0.5 | 5.45 | 2.78% |
| COUNT(*) | 1.0 | 3.54 | 1.80% |
| COUNT(*) | 2.0 | 2.63 | 1.34% |
| COUNT(*) | 4.0 | 1.78 | 0.91% |
| SUM(total_charge) | 0.01 | 39,150.10 | 31.29% |
| SUM(total_charge) | 0.02 | 26,114.01 | 20.87% |
| SUM(total_charge) | 0.05 | 17,178.74 | 13.73% |
| SUM(total_charge) | 0.1 | 11,030.13 | 8.82% |
| SUM(total_charge) | 0.25 | 8,295.07 | 6.63% |
| SUM(total_charge) | 0.5 | 5,168.33 | 4.13% |
| SUM(total_charge) | 1.0 | 3,587.96 | 2.87% |
| SUM(total_charge) | 2.0 | 2,922.36 | 2.34% |
| SUM(total_charge) | 4.0 | 1,925.34 | 1.54% |
| AVG(total_charge) | 0.01 | 1,078.08 | 168.91% |
| AVG(total_charge) | 0.02 | 538.29 | 84.34% |
| AVG(total_charge) | 0.05 | 140.67 | 22.04% |
| AVG(total_charge) | 0.1 | 92.22 | 14.45% |
| AVG(total_charge) | 0.25 | 71.17 | 11.15% |
| AVG(total_charge) | 0.5 | 43.49 | 6.81% |
| AVG(total_charge) | 1.0 | 31.61 | 4.95% |
| AVG(total_charge) | 2.0 | 21.86 | 3.43% |
| AVG(total_charge) | 4.0 | 15.20 | 2.38% |
