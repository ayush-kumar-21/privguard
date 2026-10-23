"""
Noise mechanisms over a deterministic keyed stream. `SPEC.md` §4.4.

Noise for a query is derived deterministically from a per-query seed:

    seed = SHA-256( server_secret || ast_hash || principal_id_be64 || salt )

This is what makes the noise cache *sound*. The same fingerprint and principal
always reproduce the same answer, so "ask it a thousand times and average the
noise away" fails. A clock-seeded RNG would hand the analyst a thousand
independent samples and defeat the whole design.

The stream is SHA-256 in counter mode: deterministic where we need it, and not
predictable without the server secret. (The research plan specifies ChaCha20;
SHA-256-CTR is the stdlib-only equivalent used here, and the paper says so.)

**No floating-point sampling.** Noise from `-b*log(1-2|u|)` or Box-Muller is not
differentially private: the set of floats it can emit depends on the true value,
which an observer can detect (Mironov, CCS 2012; Jin et al., S&P 2022 for the
Gaussian). Here every answer is moved onto an integer grid of step `unit / GRID`,
noised with the *exact* discrete Laplace or discrete Gaussian of Canonne, Kamath
& Steinke (NeurIPS 2020, Algs. 1-3), which use integer arithmetic only, and moved
back. The released value is `cells * step`, a function of an integer alone, so no
low-order float bit depends on the data. `SPEC.md` §4.4 and Amendment A5.
"""
from __future__ import annotations

import hashlib
import math

__all__ = [
    "GRID",
    "KeyedRandom",
    "derive_seed",
    "dlap",
    "dgauss",
    "noisy_laplace",
    "noisy_gaussian",
    "noisy_count",
    "partition_cutoff",
    "partition_cells",
    "clamp",
]

#: Grid cells per `unit` (the per-privacy-unit L-infinity contribution bound).
GRID = 1024

# Parameter ceilings keep every intermediate below 2**127 (the Rust engine uses u128).
# They are reached only at a per-stage rho below ~5e-10, which is refused.
_MAX_LAPLACE_SCALE = 1 << 40
_MAX_GAUSSIAN_VARIANCE = 1 << 50


def derive_seed(
    server_secret: bytes, ast_hash: bytes, principal_id: int, salt: bytes = b""
) -> bytes:
    """The per-query noise seed. `SPEC.md` §4.4.

    Including the principal id means two analysts asking the identical question
    get independently-seeded answers, so neither can average against the other.
    """
    return hashlib.sha256(
        server_secret + ast_hash + principal_id.to_bytes(8, "big") + salt
    ).digest()


class KeyedRandom:
    """Deterministic byte stream: SHA-256(seed || counter_be64), read 8 bytes at a time.

    Not thread-safe, and deliberately so: two threads sharing a stream would
    consume each other's draws and make the output non-reproducible, which is
    the one property this class exists to provide.
    """

    __slots__ = ("_seed", "_counter", "_buf")

    def __init__(self, seed: bytes):
        self._seed = seed
        self._counter = 0
        self._buf = b""

    def _refill(self) -> None:
        block = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        self._counter += 1
        self._buf += block

    def next_u64(self) -> int:
        while len(self._buf) < 8:
            self._refill()
        chunk, self._buf = self._buf[:8], self._buf[8:]
        return int.from_bytes(chunk, "big")

    def next_u128(self) -> int:
        """Two consecutive 64-bit words, the first one the high half."""
        return (self.next_u64() << 64) | self.next_u64()

    def below(self, n: int) -> int:
        """Uniform integer in [0, n) by rejection (no modulo bias). `n == 1` draws nothing."""
        if n < 1:
            raise ValueError("n must be positive")
        if n == 1:
            return 0
        limit = (1 << 128) - ((1 << 128) % n)
        while True:
            x = self.next_u128()
            if x < limit:
                return x % n


# --- exact samplers (Canonne, Kamath & Steinke 2020) -------------------------
def _bern_unit(p: int, q: int, rng: KeyedRandom) -> bool:
    """Bernoulli(exp(-p/q)) for 0 <= p <= q (Alg. 1)."""
    k = 1
    while rng.below(q * k) < p:
        k += 1
    return k % 2 == 1


def _bern_exp(p: int, q: int, rng: KeyedRandom) -> bool:
    """Bernoulli(exp(-p/q)) for any p >= 0."""
    while p > q:
        if not _bern_unit(1, 1, rng):
            return False
        p -= q
    return _bern_unit(p, q, rng)


def dlap(t: int, rng: KeyedRandom) -> int:
    """Z with P(Z = z) proportional to exp(-|z| / t), t a positive integer (Alg. 2, s = 1)."""
    if t < 1:
        raise ValueError("scale must be a positive integer")
    while True:
        u = rng.below(t)
        if not _bern_exp(u, t, rng):
            continue
        v = 0
        while _bern_unit(1, 1, rng):
            v += 1
        x = u + t * v
        negative = rng.below(2) == 1
        if negative and x == 0:
            continue
        return -x if negative else x


def dgauss(variance: int, rng: KeyedRandom) -> int:
    """Z with P(Z = z) proportional to exp(-z^2 / (2 variance)), variance a positive integer (Alg. 3)."""
    if variance < 1:
        raise ValueError("variance must be a positive integer")
    t = math.isqrt(variance) + 1  # floor(sigma) + 1
    while True:
        y = dlap(t, rng)
        # gamma = (|y| - variance/t)^2 / (2 variance) = (|y| t - variance)^2 / (2 variance t^2)
        if _bern_exp((abs(y) * t - variance) ** 2, 2 * variance * t * t, rng):
            return y


# --- parameters: real rho -> conservative integers ---------------------------
def _laplace_scale(rho: float, cells: int) -> int:
    """Integer scale T with cells / T <= sqrt(2 rho): pure eps no larger than rho-zCDP allows."""
    if not (rho > 0 and math.isfinite(rho)):
        raise ValueError("rho must be positive and finite")
    t = math.ceil(cells / math.sqrt(2.0 * rho))
    if t > _MAX_LAPLACE_SCALE:
        raise ValueError("rho is too small for exact sampling")
    return max(t, 1)


def _gaussian_variance(rho: float, cells_sq: int) -> int:
    """Integer variance V with cells_sq / (2 V) <= rho."""
    if not (rho > 0 and math.isfinite(rho)):
        raise ValueError("rho must be positive and finite")
    v = math.ceil(cells_sq / (2.0 * rho))
    if v > _MAX_GAUSSIAN_VARIANCE:
        raise ValueError("rho is too small for exact sampling")
    return max(v, 1)


def _step(unit: float) -> float:
    if not (unit > 0 and math.isfinite(unit)):
        raise ValueError("unit must be positive and finite")
    return unit / GRID


def _cells(value: float, step: float) -> int:
    """Nearest grid cell, ties up (not Python's banker's `round`, so Rust can match it)."""
    return math.floor(value / step + 0.5)


def noisy_laplace(value: float, unit: float, rho: float, rng: KeyedRandom) -> float:
    """rho-zCDP release of `value` whose L1 sensitivity is `unit`.

    Sensitivity in cells is GRID + 1: GRID for the data, one more for rounding
    `value` onto the grid.
    """
    step = _step(unit)
    cells = _cells(value, step) + dlap(_laplace_scale(rho, GRID + 1), rng)
    return cells * step


def noisy_count(n: int, unit: float, rho: float, rng: KeyedRandom) -> int:
    """`noisy_laplace` for an integer count, rounded back to an integer (post-processing)."""
    return math.floor(noisy_laplace(float(n), unit, rho, rng) + 0.5)


def noisy_gaussian(
    value: float, unit: float, groups: int, rho: float, rng: KeyedRandom
) -> float:
    """rho-zCDP release of one coordinate of a vector with at most `groups` coordinates
    each moving by at most `unit` (L2 sensitivity sqrt(groups) * unit)."""
    step = _step(unit)
    variance = _gaussian_variance(rho, groups * (GRID + 1) ** 2)
    return (_cells(value, step) + dgauss(variance, rng)) * step


# --- partition selection ----------------------------------------------------
def partition_cutoff(rho_part: float, delta_part: float) -> tuple[int, int]:
    """(scale T, cutoff K) for DP partition selection. `SPEC.md` §4.5.

    Counts are integers, so they sit exactly on a grid of 1/GRID and need no
    rounding slack: sensitivity is GRID cells. A group holding a single unit is
    released iff its noise is >= K, with probability
    e^(-K/T) / (1 + e^(-1/T)) <= delta_part (the discrete Laplace tail).
    """
    t = _laplace_scale(rho_part, GRID)
    k = math.ceil(t * math.log(1.0 / (delta_part * (1.0 + math.exp(-1.0 / t)))))
    return t, max(k, 1)


def partition_cells(units: int, t: int, rng: KeyedRandom) -> int:
    """`units` plus noise, in cells of 1/GRID; release iff the result is >= GRID + K."""
    return units * GRID + dlap(t, rng)


def clamp(value: float, lo: float | None, hi: float | None) -> float:
    """Clamp into [lo, hi]. This is what gives an aggregate finite sensitivity."""
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return value
