"""Experiments 1 and 2 — no database needed.

Exp 1  Collusion: K analysts ask the same COUNT and average their independent
       noisy answers. Measures attacker RMSE vs K, with and without the global
       budget cap (rho_global_cap) that sp_reserve_budget enforces.
Exp 2  Partition selection: the old code ignored the L0 bound G. Measures
       (a) the empirical probability that a *lone unit's* partition is released
           (the delta guarantee) for the old and the new parameters, and
       (b) the utility cost: probability a real group is released vs its size.
"""
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from common import OUT, seed, wilson  # noqa: E402
from privacy import mechanisms as M  # noqa: E402
from privacy.mechanisms import KeyedRandom  # noqa: E402
from privacy.service import partition_params  # noqa: E402

rng_np = np.random.default_rng(12345)


# ----------------------------------------------------------------- Exp 1
def exp_collusion():
    T, C, RHO = 50.0, 5, 0.05  # true count, contribution bound, per-account rho
    CAP = 0.5  # global cap: room for 10 accounts of rho=0.05
    allowed = int(round(CAP / RHO))
    trials, Ks = 400, [1, 2, 4, 8, 16, 32, 64]
    rows = []
    for K in Ks:
        for label, k_eff in (("no global cap", K), ("global cap", min(K, allowed))):
            sq = []
            for t in range(trials):
                ans = [
                    M.noisy_laplace(T, C, RHO, KeyedRandom(seed("collude", t, p)))
                    for p in range(k_eff)
                ]
                sq.append((sum(ans) / k_eff - T) ** 2)
            sq = np.array(sq)
            boots = [math.sqrt(rng_np.choice(sq, len(sq)).mean()) for _ in range(300)]
            rows.append((K, label, k_eff, math.sqrt(sq.mean()),
                         np.percentile(boots, 2.5), np.percentile(boots, 97.5)))
    sigma1 = [r[3] for r in rows if r[0] == 1 and r[1] == "no global cap"][0]

    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    for label, color in (("no global cap", "#c0392b"), ("global cap", "#27ae60")):
        pts = [r for r in rows if r[1] == label]
        ax.errorbar(
            [p[0] for p in pts], [p[3] for p in pts],
            yerr=[[p[3] - p[4] for p in pts], [p[5] - p[3] for p in pts]],
            marker="o", capsize=3, label=label, color=color,
        )
    ax.plot(Ks, [sigma1 / math.sqrt(k) for k in Ks], "k--", lw=1, label=r"$\sigma/\sqrt{K}$")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("colluding analysts K (each with its own budget)")
    ax.set_ylabel("attacker RMSE on a true count of 50")
    ax.set_title("Exp 1: averaging across colluding accounts")
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{OUT}/exp1_collusion.png", dpi=150)

    print("\nEXP 1  collusion (true count 50, rho/account 0.05, cap 0.5 -> 10 accounts)")
    print(f"{'K':>4} {'setting':<14} {'accounts used':>13} {'RMSE':>8}   95% CI")
    for K, label, k_eff, rmse, lo, hi in rows:
        print(f"{K:>4} {label:<14} {k_eff:>13} {rmse:>8.2f}   [{lo:.2f}, {hi:.2f}]")


# ----------------------------------------------------------------- Exp 2
def legacy_params(rho_part, delta):
    """The previous code: eps0 = sqrt(2*rho_part) for every partition, no G."""
    eps = math.sqrt(2.0 * rho_part)
    scale = 1.0 / eps
    return scale, 1.0 + scale * math.log(1.0 / (2.0 * delta))


def exp_partition():
    G, DELTA, RHO = 3, 1e-2, 0.05
    rho_part = RHO * 0.2

    # (a) delta audit: a partition holding ONE unit, which sits in G partitions.
    N = 60_000
    scale, tau_old = legacy_params(rho_part, DELTA)
    old_hits = int((1 + rng_np.laplace(0, scale, size=(N, G)) > tau_old).any(axis=1).sum())
    eps, tau_new = partition_params(rho_part, G, DELTA)
    r = KeyedRandom(seed("partition-audit"))
    new_hits = 0
    for _ in range(N):
        if any(
            1 + M.sample_discrete_laplace(eps.numerator, eps.denominator, r) > tau_new
            for _ in range(G)
        ):
            new_hits += 1
    ci_old, ci_new = wilson(old_hits, N), wilson(new_hits, N)
    print(f"\nEXP 2a delta audit  (G={G}, target delta={DELTA}, rho={RHO}, N={N} trials)")
    print(f"  old: P(lone unit released) = {old_hits/N:.4f}  95% CI "
          f"[{ci_old[0]:.4f}, {ci_old[1]:.4f}]   -> {old_hits/N/DELTA:.1f}x the target")
    print(f"  new: P(lone unit released) = {new_hits/N:.4f}  95% CI "
          f"[{ci_new[0]:.4f}, {ci_new[1]:.4f}]   -> {new_hits/N/DELTA:.1f}x the target")
    print(f"  old effective eps per unit = {G*math.sqrt(2*rho_part):.3f} vs claimed "
          f"{math.sqrt(2*rho_part):.3f}  (zCDP cost {G}x: {G*rho_part:.3f} vs {rho_part:.3f})")

    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    vals = [old_hits / N, new_hits / N]
    errs = [[vals[0] - ci_old[0], vals[1] - ci_new[0]],
            [ci_old[1] - vals[0], ci_new[1] - vals[1]]]
    ax.bar(["old (G-unaware)", "new (L0-aware)"], vals, yerr=errs, capsize=5,
           color=["#c0392b", "#27ae60"])
    ax.axhline(DELTA, color="k", ls="--", lw=1, label=f"target delta = {DELTA}")
    ax.set_ylabel("P(lone-unit partition released)")
    ax.set_title("Exp 2a: delta guarantee, empirically")
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{OUT}/exp2a_delta.png", dpi=150)

    # (b) utility: probability that a real group of u units survives thresholding.
    units = [5, 10, 20, 40, 80, 160]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True)
    print("\nEXP 2b utility: P(group released) by group size (units), G=3, delta=1e-2")
    for ax, rho in zip(axes, (0.05, 0.2, 1.0)):
        rp = rho * 0.2
        s_old, t_old = legacy_params(rp, DELTA)
        e_new, t_new = partition_params(rp, G, DELTA)
        p_old, p_new = [], []
        rr = KeyedRandom(seed("utility", rho))
        for u in units:
            p_old.append(float((u + rng_np.laplace(0, s_old, 4000) > t_old).mean()))
            hits = sum(
                u + M.sample_discrete_laplace(e_new.numerator, e_new.denominator, rr) > t_new
                for _ in range(1500)
            )
            p_new.append(hits / 1500)
        ax.plot(units, p_old, "o-", color="#c0392b", label="old")
        ax.plot(units, p_new, "s-", color="#27ae60", label="new")
        ax.set_xscale("log", base=2)
        ax.set_title(f"rho = {rho}  (tau old {t_old:.0f}, new {t_new:.0f})")
        ax.set_xlabel("units in group")
        print(f"  rho={rho}: tau old={t_old:.1f} new={t_new:.1f}")
        for u, a, b in zip(units, p_old, p_new):
            print(f"     units={u:>3}  old {a:5.2f}  new {b:5.2f}")
    axes[0].set_ylabel("P(group released)")
    axes[0].legend()
    fig.suptitle("Exp 2b: the utility cost of correct partition selection")
    fig.tight_layout()
    fig.savefig(f"{OUT}/exp2b_utility.png", dpi=150)


if __name__ == "__main__":
    exp_collusion()
    exp_partition()
