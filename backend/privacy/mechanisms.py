"""
Noise mechanisms with a deterministic, CSPRNG-grade keyed stream.

The noise for a query is derived deterministically from a per-query seed
(server_secret || ast_hash || principal_id). This is what makes *noise
memoisation* correct: the same fingerprint + principal always reproduces the
same answer, so "ask it 1000 times and average the noise away" fails.

We use a SHA-256 keyed counter stream (HMAC-like) rather than `random` seeded
from the clock — deterministic where we need it, and not predictable without
the server secret. (The research plan specifies ChaCha20; SHA-256-CTR is the
stdlib-only equivalent used here and noted as such in the paper.)
"""
from __future__ import annotations

import hashlib
import math


class KeyedRandom:
    """Uniform(0,1) stream from SHA-256(seed || counter)."""

    def __init__(self, seed: bytes):
        self._seed = seed
        self._counter = 0
        self._buf = b""

    def _refill(self) -> None:
        block = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        self._counter += 1
        self._buf += block

    def uniform(self) -> float:
        """53-bit uniform float in [0, 1)."""
        while len(self._buf) < 8:
            self._refill()
        chunk, self._buf = self._buf[:8], self._buf[8:]
        return (int.from_bytes(chunk, "big") >> 11) / float(1 << 53)


def laplace(scale: float, rng: KeyedRandom) -> float:
    """Sample Laplace(0, scale) via inverse CDF."""
    if scale <= 0:
        return 0.0
    u = rng.uniform() - 0.5  # (-0.5, 0.5)
    if u == 0.0:
        return 0.0
    return -scale * math.copysign(1.0, u) * math.log(1.0 - 2.0 * abs(u))


def gaussian(sigma: float, rng: KeyedRandom) -> float:
    """Sample N(0, sigma^2) via Box-Muller."""
    if sigma <= 0:
        return 0.0
    u1 = max(rng.uniform(), 1e-12)
    u2 = rng.uniform()
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return sigma * z


def discrete_laplace(scale: float, rng: KeyedRandom) -> int:
    """Integer-valued noise for counts (rounds Laplace; avoids float LSB leakage)."""
    return int(round(laplace(scale, rng)))


def clamp(value: float, lo: float | None, hi: float | None) -> float:
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return value
