"""
zCDP privacy accounting. `SPEC.md` §4.1.

Composition under zero-concentrated DP is additive in rho, which is the whole
reason the budget ledger can be a running sum rather than a composition theorem
evaluated over a query history.

    Gaussian   rho = D2^2 / (2*sigma^2)     ->  sigma = D2 / sqrt(2*rho)
    Laplace    eps0-DP is (eps0^2/2)-zCDP   ->  eps0  = sqrt(2*rho), b = D1/eps0
    Reporting  eps(delta) = rho + 2*sqrt(rho * ln(1/delta))

Mirrored in `engine/crates/privguard-privacy/src/accounting.rs`. The duplication
is deliberate: two independent implementations catch mistakes one would not.
"""
from __future__ import annotations

import math

__all__ = [
    "sigma_from_rho",
    "laplace_scale_from_rho",
    "eps_from_rho",
    "eps0_from_rho",
    "grouped_l2_sensitivity",
]


def sigma_from_rho(delta2: float, rho: float) -> float:
    """Gaussian scale sigma for a target rho and L2 sensitivity.

    A non-positive rho buys no budget and so demands infinite noise. Returning
    inf rather than raising keeps caller arithmetic total; any answer computed
    from it is inf, which is caught before release rather than silently output.
    """
    if rho <= 0:
        return math.inf
    return delta2 / math.sqrt(2.0 * rho)


def laplace_scale_from_rho(delta1: float, rho: float) -> float:
    """Laplace scale b = D1 / eps0, where eps0 = sqrt(2*rho)."""
    if rho <= 0:
        return math.inf
    return delta1 / math.sqrt(2.0 * rho)


def eps0_from_rho(rho: float) -> float:
    """eps0 of the pure-DP mechanism underlying a rho-zCDP Laplace spend."""
    if rho <= 0:
        return 0.0
    return math.sqrt(2.0 * rho)


def eps_from_rho(rho: float, delta: float) -> float:
    """The (eps, delta) equivalent of a rho spend — for reporting only.

    Budgets are held and spent in rho. This projection exists because people
    think in eps. Never accumulate the result: eps does not compose additively,
    which is exactly the trap zCDP is used to avoid.
    """
    if rho <= 0:
        return 0.0
    return rho + 2.0 * math.sqrt(rho * math.log(1.0 / delta))


def grouped_l2_sensitivity(max_groups: int, per_group_delta1: float) -> float:
    """L2 sensitivity of a grouped aggregate: sqrt(G) * per-group L1.

    The sqrt(G) rather than G is the point of using the Gaussian for grouped
    queries: an L0 bound of G groups costs sqrt(G) in L2 but a full G in L1.
    """
    return math.sqrt(max_groups) * per_group_delta1
