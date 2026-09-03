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
"""
from __future__ import annotations

import hashlib
import math

__all__ = ["KeyedRandom", "derive_seed", "laplace", "gaussian", "discrete_laplace", "clamp"]


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
    """Uniform [0, 1) stream from SHA-256(seed || counter).

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

    def uniform(self) -> float:
        """53-bit uniform float in [0, 1).

        53 bits because that is a float64's mantissa: taking more would suggest
        a precision the type cannot represent, and taking the *top* bits avoids
        the low-bit bias of a modulo reduction.
        """
        while len(self._buf) < 8:
            self._refill()
        chunk, self._buf = self._buf[:8], self._buf[8:]
        return (int.from_bytes(chunk, "big") >> 11) / float(1 << 53)


def laplace(scale: float, rng: KeyedRandom) -> float:
    """Sample Laplace(0, scale) by inverse CDF."""
    if scale <= 0:
        return 0.0
    u = rng.uniform() - 0.5  # (-0.5, 0.5]
    if u == 0.0:
        return 0.0
    return -scale * math.copysign(1.0, u) * math.log(1.0 - 2.0 * abs(u))


def gaussian(sigma: float, rng: KeyedRandom) -> float:
    """Sample N(0, sigma^2) by Box-Muller.

    u1 is floored away from zero because log(0) is -inf; the clamp is far below
    any value the 53-bit stream produces in practice.
    """
    if sigma <= 0:
        return 0.0
    u1 = max(rng.uniform(), 1e-12)
    u2 = rng.uniform()
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return sigma * z


def discrete_laplace(scale: float, rng: KeyedRandom) -> int:
    """Integer-valued noise for counts.

    Rounding avoids leaking through the float's low bits: a released count of
    41.99999999999999 says something about the noise draw that 42 does not.
    """
    return int(round(laplace(scale, rng)))


def clamp(value: float, lo: float | None, hi: float | None) -> float:
    """Clamp into [lo, hi]. This is what gives an aggregate finite sensitivity."""
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return value
