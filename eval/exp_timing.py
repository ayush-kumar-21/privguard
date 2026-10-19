"""Experiment 3 — can an analyst tell a small slice from a large one by response time?

Run through `manage.py shell` against the seeded database (see eval/run_all.sh).

The attacker issues many *fresh* COUNT queries (a unique threshold in the WHERE
clause defeats the cache without changing the slice) against two slices of very
different true size, and measures wall-clock time. The distinguishing power is
reported as AUC: 0.5 = no information, 1.0 = perfect classification.
Measured with and without PRIVGUARD_MIN_RESPONSE_MS (the response-time floor).
"""
import math
import os
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from django.conf import settings

from accounts.models import Principal
from budget.models import BudgetAccount
from catalog.models import SysDatabase
from privacy.service import run_private_query

OUT = "/repo/docs/eval"
os.makedirs(OUT, exist_ok=True)

N = 120  # queries per slice per setting
alice = Principal.objects.get(username="alice")
db = SysDatabase.objects.get(db_name="safehealth")
db.rho_global_cap = None
db.save(update_fields=["rho_global_cap"])
BudgetAccount.objects.filter(principal=alice, db=db).update(rho_total=1e6)

SLICES = {
    "small": "SELECT COUNT(*) FROM visits WHERE admission_type = 'EMERGENCY' AND dept_id = 1 "
             "AND total_charge > {t}",
    "large": "SELECT COUNT(*) FROM visits WHERE total_charge > {t}",
}
counter = [0]


def timed(kind: str) -> float:
    counter[0] += 1
    sql = SLICES[kind].format(t=f"{1 + counter[0] * 1e-6:.6f}")
    start = time.perf_counter()
    resp = run_private_query(alice, "safehealth", sql, rho_req=0.05)
    elapsed = (time.perf_counter() - start) * 1000.0
    assert resp["status"] == "executed", resp["status"]
    return elapsed


def auc(a, b) -> float:
    """P(random sample from b is slower than one from a) via the rank-sum statistic."""
    a, b = np.asarray(a), np.asarray(b)
    greater = (b[:, None] > a[None, :]).sum() + 0.5 * (b[:, None] == a[None, :]).sum()
    return greater / (len(a) * len(b))


def boot_ci(a, b, reps=1000, seed=7):
    rng = np.random.default_rng(seed)
    vals = [auc(rng.choice(a, len(a)), rng.choice(b, len(b))) for _ in range(reps)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


results = {}
for label, floor in (("no floor (0 ms)", 0), ("floor 150 ms", 150)):
    settings.PRIVGUARD_MIN_RESPONSE_MS = floor
    for _ in range(5):  # warm-up
        timed("small"), timed("large")
    small = [timed("small") for _ in range(N)]
    large = [timed("large") for _ in range(N)]
    a = auc(small, large)
    lo, hi = boot_ci(small, large)
    results[label] = (small, large, a, lo, hi)

print("\nEXP 3  timing channel: can the analyst tell a small slice from a large one?")
print(f"  ({N} fresh queries per slice per setting; AUC 0.5 = no information)")
for label, (small, large, a, lo, hi) in results.items():
    print(f"  {label:<16} small median {np.median(small):7.1f} ms | large median "
          f"{np.median(large):7.1f} ms | AUC = {a:.3f}  95% CI [{lo:.3f}, {hi:.3f}]  "
          f"-> {'DETECTABLE' if not (lo <= 0.5 <= hi) else 'not detectable'}")

fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8))
for ax, (label, (small, large, a, lo, hi)) in zip(axes, results.items()):
    hi_ms = max(max(small), max(large))
    bins = np.linspace(min(min(small), min(large)) * 0.95, hi_ms * 1.02, 40)
    ax.hist(small, bins=bins, alpha=0.6, label="small slice", color="#2980b9")
    ax.hist(large, bins=bins, alpha=0.6, label="large slice", color="#e67e22")
    ax.set_title(f"{label}\nAUC = {a:.2f}  [{lo:.2f}, {hi:.2f}]")
    ax.set_xlabel("response time (ms)")
axes[0].set_ylabel("queries")
axes[0].legend()
fig.suptitle("Exp 3: timing side channel, with and without the response-time floor")
fig.tight_layout()
fig.savefig(f"{OUT}/exp3_timing.png", dpi=150)
