"""Reproduce docs/eval/algorithms.md: what the two algorithm upgrades buy.

    python3 tools/eval_algorithms.py            # prints the markdown; add --write to update the file

Standard library only. Needs the golden dataset (oracle/tests/golden/dataset.json).
"""
import json
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from privacy.accounting import eps_from_rho, eps_from_rho_classic  # noqa: E402

TRIALS = 20_000


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def exact_gaussian_eps(rho, delta):
    mu, lo, hi = math.sqrt(2 * rho), 0.0, eps_from_rho_classic(rho, delta)
    for _ in range(100):
        mid = (lo + hi) / 2
        d = _phi(-mid / mu + mu / 2) - math.exp(mid) * _phi(-mid / mu - mu / 2)
        lo, hi = (mid, hi) if d > delta else (lo, mid)
    return hi


def laplace(b, rnd):
    u = rnd.random() - 0.5
    return -b * math.copysign(1, u) * math.log(1 - 2 * abs(u))


def avg_rmse(rows, lo, hi, C, rho, centred, seed=7):
    x = [min(max(r["total_charge"], lo), hi) for r in rows if r["total_charge"] is not None]
    n, truth = len(x), sum(x) / len(x)
    centre = (hi + lo) / 2 if centred else 0.0
    sens = C * ((hi - lo) / 2 if centred else max(abs(lo), abs(hi)))
    eps0 = math.sqrt(2 * (rho / 2))
    rnd, errs = random.Random(seed), []
    base = sum(v - centre for v in x)
    for _ in range(TRIALS):
        s = base + laplace(sens / eps0, rnd)
        c = n + laplace(C / eps0, rnd)
        errs.append((centre + s / max(c, 1.0) - truth) ** 2)
    mse = statistics.fmean(errs)
    se_mse = math.sqrt(statistics.pvariance(errs) / TRIALS)
    return truth, math.sqrt(mse), se_mse / (2 * math.sqrt(mse))  # delta method


def main():
    out = ["# What the algorithm upgrades buy", "",
           "Regenerate with `python3 tools/eval_algorithms.py --write`.", "",
           "## 1. Tighter budget-to-epsilon conversion (reporting only; the noise is unchanged)", "",
           "| rho | delta | classic eps | tighter eps | exact-Gaussian floor | lower by |", "|---:|---:|---:|---:|---:|---:|"]
    for rho, delta in [(0.05, 1e-6), (0.5, 1e-6), (5.0, 1e-6), (5.0, 1e-9)]:
        c, t, g = eps_from_rho_classic(rho, delta), eps_from_rho(rho, delta), exact_gaussian_eps(rho, delta)
        out.append(f"| {rho} | {delta:.0e} | {c:.3f} | {t:.3f} | {g:.3f} | {100 * (c - t) / c:.1f}% |")
    out += ["", "The floor is the exact epsilon of a Gaussian mechanism with that rho; no valid conversion can go below it.", "",
            "## 2. Centred mean for AVG (same privacy cost, less noise)", "",
            f"Golden dataset, {TRIALS:,} noise draws per row. RMSE of AVG(total_charge); the last column is the standard error of the RMSE.", "",
            "| clamp | C | rho | true mean | plain-sum RMSE | centred RMSE | lower by | s.e. |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    rows = json.loads((ROOT / "oracle/tests/golden/dataset.json").read_text())
    for (lo, hi, C, rho) in [(0.0, 1000.0, 5, 0.5), (0.0, 1000.0, 5, 2.0), (-50.0, 400.0, 2, 0.5)]:
        truth, old, _ = avg_rmse(rows, lo, hi, C, rho, centred=False)
        _, new, se = avg_rmse(rows, lo, hi, C, rho, centred=True)
        out.append(f"| [{lo:g}, {hi:g}] | {C} | {rho} | {truth:.1f} | {old:.1f} | {new:.1f} | {100 * (old - new) / old:.0f}% | {se:.2f} |")
    text = "\n".join(out) + "\n"
    if "--write" in sys.argv:
        (ROOT / "docs/eval/algorithms.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
