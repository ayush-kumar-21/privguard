"""
zCDP privacy accounting.

Composition is additive in rho, which is exactly why the budget ledger is a
running sum. Conversions:

  Gaussian  : rho = Delta2^2 / (2*sigma^2)   ->  sigma = Delta2 / sqrt(2*rho)
  Laplace   : eps0-DP is (eps0^2/2)-zCDP     ->  eps0  = sqrt(2*rho),  b = Delta1/eps0
  Reporting : eps(delta) = rho + 2*sqrt(rho * ln(1/delta))
"""
from __future__ import annotations

import math


def sigma_from_rho(delta2: float, rho: float) -> float:
    """Gaussian scale for a target rho and L2 sensitivity."""
    if rho <= 0:
        return float("inf")
    return delta2 / math.sqrt(2.0 * rho)


def laplace_scale_from_rho(delta1: float, rho: float) -> float:
    """Laplace scale b = Delta1 / eps0 where eps0 = sqrt(2*rho)."""
    if rho <= 0:
        return float("inf")
    return delta1 / math.sqrt(2.0 * rho)


def eps_from_rho(rho: float, delta: float) -> float:
    """(eps, delta) equivalent of a zCDP spend, for human-readable reporting."""
    if rho <= 0:
        return 0.0
    return rho + 2.0 * math.sqrt(rho * math.log(1.0 / delta))
