"""
Exact noise mechanisms over the integers — no floating-point sampling.

Why not `-b*sign(u)*log(1-2|u|)`?  Sampling Laplace or Gaussian noise with
floating-point arithmetic is *not* differentially private: the set of
representable outputs depends on the true value, which an observer can detect
(Mironov, CCS 2012; Jin et al., IEEE S&P 2022 extends it to the Gaussian).

Instead we use the exact discrete Laplace and discrete Gaussian samplers of
Canonne, Kamath & Steinke, "The Discrete Gaussian for Differential Privacy"
(NeurIPS 2020), built from integer arithmetic and `Fraction` only. A query
answer is first moved onto an integer grid, noised exactly, and moved back; all
privacy parameters are rounded in the *conservative* direction (more noise),
so the stated rho is never understated.

Randomness comes from a SHA-256 keyed counter stream so that the same
(server secret, query, principal) always reproduces the same noise. That is
what makes noise memoisation correct: asking again returns the cached answer,
and "ask it 1000 times and average the noise away" fails.

Residual caveat: the rejection loops run for a data-independent but
randomised number of iterations, which is observable as response time.
`service.run_private_query` therefore pads responses to a fixed floor
(PRIVGUARD_MIN_RESPONSE_MS).
"""
from __future__ import annotations

import hashlib
import math
from fractions import Fraction

# Fixed-point denominators used when turning a real-valued privacy parameter into
# a rational. 2**20 keeps the rounding error negligible; rounding is always in
# the direction that adds more noise.
_PARAM_DENOM = 1 << 20

# Granularity of the integer grid that real-valued sums are moved onto.
SUM_GRID = 1.0 / 1024.0


class KeyedRandom:
    """Deterministic byte stream: SHA-256(seed || counter)."""

    def __init__(self, seed: bytes):
        self._seed = seed
        self._counter = 0
        self._buf = b""

    def _bytes(self, n: int) -> bytes:
        while len(self._buf) < n:
            block = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
            self._counter += 1
            self._buf += block
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def randbelow(self, n: int) -> int:
        """Uniform integer in [0, n), by rejection sampling (no modulo bias)."""
        if n <= 0:
            raise ValueError("n must be positive")
        if n == 1:
            return 0
        bits = (n - 1).bit_length()
        nbytes = (bits + 7) // 8
        mask = (1 << bits) - 1
        while True:
            x = int.from_bytes(self._bytes(nbytes), "big") & mask
            if x < n:
                return x

    def bernoulli(self, p: Fraction) -> bool:
        """True with probability p (a Fraction in [0, 1])."""
        if p <= 0:
            return False
        if p >= 1:
            return True
        return self.randbelow(p.denominator) < p.numerator


# --- Bernoulli(exp(-gamma)) for rational gamma >= 0 (CKS20, Alg. 1) --------
def _bernoulli_exp_unit(gamma: Fraction, rng: KeyedRandom) -> bool:
    """Bernoulli(exp(-gamma)) for gamma in [0, 1]."""
    k = 1
    while rng.bernoulli(gamma / k):
        k += 1
    return k % 2 == 1


def bernoulli_exp(gamma: Fraction, rng: KeyedRandom) -> bool:
    """Bernoulli(exp(-gamma)) for any rational gamma >= 0."""
    if gamma < 0:
        raise ValueError("gamma must be non-negative")
    while gamma > 1:
        if not _bernoulli_exp_unit(Fraction(1), rng):
            return False
        gamma -= 1
    return _bernoulli_exp_unit(gamma, rng)


# --- Discrete Laplace (CKS20, Alg. 2) --------------------------------------
def sample_discrete_laplace(s: int, t: int, rng: KeyedRandom) -> int:
    """Z with P(Z=z) proportional to exp(-|z| * s / t); scale t/s."""
    if s <= 0 or t <= 0:
        raise ValueError("s and t must be positive integers")
    while True:
        u = rng.randbelow(t)
        if not bernoulli_exp(Fraction(u, t), rng):
            continue
        v = 0
        while bernoulli_exp(Fraction(1), rng):
            v += 1
        y = (u + t * v) // s
        negative = rng.bernoulli(Fraction(1, 2))
        if negative and y == 0:
            continue
        return -y if negative else y


# --- Discrete Gaussian (CKS20, Alg. 3) -------------------------------------
def sample_discrete_gaussian(sigma_sq: Fraction, rng: KeyedRandom) -> int:
    """Z with P(Z=z) proportional to exp(-z^2 / (2 sigma^2))."""
    if sigma_sq <= 0:
        raise ValueError("sigma^2 must be positive")
    t = math.isqrt(int(sigma_sq)) + 1  # floor(sigma) + 1
    while True:
        y = sample_discrete_laplace(1, t, rng)
        gamma = (Fraction(abs(y)) - sigma_sq / t) ** 2 / (2 * sigma_sq)
        if bernoulli_exp(gamma, rng):
            return y


# --- Parameter handling: real -> conservative rational ----------------------
def _round_down_fraction(x: float) -> Fraction:
    """A positive rational <= x with ~20 bits of relative precision.

    The denominator grows until x*denominator has at least 20 bits, so a tiny x
    (e.g. a huge sum's epsilon-per-grid-cell) is never rounded *up* to a floor
    value — that would silently shrink the noise below what was asked for.
    """
    if not (x > 0 and math.isfinite(x)):
        raise ValueError("parameter must be positive and finite")
    den = _PARAM_DENOM
    while x * den < _PARAM_DENOM:
        den <<= 1
    return Fraction(math.floor(x * den), den)


def conservative_epsilon(eps: float) -> Fraction:
    """A rational <= eps (so the sampled noise is at least as large as asked)."""
    return _round_down_fraction(eps)


def grid_sensitivity(sensitivity: float, grid: float) -> int:
    """Sensitivity in grid units. The +1 covers rounding f(D) and f(D') onto the grid."""
    return math.ceil(sensitivity / grid) + 1 if grid != 1.0 else math.ceil(sensitivity)


def discrete_laplace_epsilon(sensitivity: float, rho: float, grid: float = 1.0) -> Fraction:
    """Per-grid-unit epsilon' used for a rho-zCDP Laplace release.

    eps0 = sqrt(2 rho) is the epsilon whose pure-DP guarantee is rho-zCDP.
    """
    eps0 = math.sqrt(2.0 * rho)
    return conservative_epsilon(eps0 / grid_sensitivity(sensitivity, grid))


def noisy_laplace(true_value: float, sensitivity: float, rho: float,
                  rng: KeyedRandom, grid: float = 1.0) -> float:
    """true_value + exact discrete Laplace noise; rho-zCDP for L1 `sensitivity`."""
    eps = discrete_laplace_epsilon(sensitivity, rho, grid)
    cells = round(true_value / grid)
    cells += sample_discrete_laplace(eps.numerator, eps.denominator, rng)
    return cells * grid


def noisy_gaussian(true_value: float, sensitivity: float, rho: float,
                   rng: KeyedRandom, grid: float = 1.0) -> float:
    """true_value + exact discrete Gaussian noise; rho-zCDP for L2 `sensitivity`."""
    delta_g = grid_sensitivity(sensitivity, grid)
    rho_q = _round_down_fraction(rho)  # <= rho
    sigma_sq = Fraction(delta_g * delta_g) / (2 * rho_q)  # >= needed
    cells = round(true_value / grid)
    cells += sample_discrete_gaussian(sigma_sq, rng)
    return cells * grid


def clamp(value: float, lo: float | None, hi: float | None) -> float:
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return value
