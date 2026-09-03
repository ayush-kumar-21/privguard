"""
Noise mechanisms and the keyed stream. `SPEC.md` §4.4.

The determinism tests here are the load-bearing ones: they are what makes the
noise cache sound, and therefore what defeats the repeat-and-average attack.
"""
from __future__ import annotations

import statistics

import pytest

from privguard_oracle.mechanisms import (
    KeyedRandom,
    clamp,
    derive_seed,
    discrete_laplace,
    gaussian,
    laplace,
)

SECRET = b"test-secret"
AST = bytes(range(32))


def test_uniform_stays_in_the_unit_interval():
    rng = KeyedRandom(b"seed")
    for _ in range(2000):
        u = rng.uniform()
        assert 0.0 <= u < 1.0


def test_the_same_seed_reproduces_the_same_stream():
    # This is the property the noise cache depends on.
    a = [KeyedRandom(b"seed").uniform() for _ in range(5)]
    b = [KeyedRandom(b"seed").uniform() for _ in range(5)]
    assert a == b

    long_a = [KeyedRandom(b"seed").uniform() for _ in range(1)]
    r = KeyedRandom(b"seed")
    assert r.uniform() == long_a[0]


def test_different_seeds_diverge():
    assert KeyedRandom(b"seed-a").uniform() != KeyedRandom(b"seed-b").uniform()


def test_stream_advances_rather_than_repeating():
    rng = KeyedRandom(b"seed")
    draws = [rng.uniform() for _ in range(64)]
    assert len(set(draws)) == len(draws), "stream repeated a value"


def test_seed_derivation_separates_principals():
    # Two analysts asking the identical question must get independently seeded
    # answers, or one could average against the other.
    s1 = derive_seed(SECRET, AST, principal_id=1)
    s2 = derive_seed(SECRET, AST, principal_id=2)
    assert s1 != s2
    assert len(s1) == 32


def test_seed_derivation_separates_salts_and_queries():
    base = derive_seed(SECRET, AST, 1)
    assert derive_seed(SECRET, AST, 1, b"partition") != base
    assert derive_seed(SECRET, bytes(32), 1) != base
    assert derive_seed(b"other-secret", AST, 1) != base


def test_seed_derivation_is_stable():
    assert derive_seed(SECRET, AST, 1) == derive_seed(SECRET, AST, 1)


@pytest.mark.parametrize("scale", [0.0, -1.0])
def test_nonpositive_scale_produces_no_noise(scale):
    assert laplace(scale, KeyedRandom(b"s")) == 0.0
    assert gaussian(scale, KeyedRandom(b"s")) == 0.0
    assert discrete_laplace(scale, KeyedRandom(b"s")) == 0


def test_laplace_is_centred_and_correctly_scaled():
    # Var[Laplace(0,b)] = 2b^2, so sd = b*sqrt(2).
    b = 2.0
    rng = KeyedRandom(b"laplace-stats")
    xs = [laplace(b, rng) for _ in range(20000)]
    assert statistics.fmean(xs) == pytest.approx(0.0, abs=0.15)
    assert statistics.pstdev(xs) == pytest.approx(b * 2**0.5, rel=0.08)


def test_gaussian_is_centred_and_correctly_scaled():
    sigma = 3.0
    rng = KeyedRandom(b"gauss-stats")
    xs = [gaussian(sigma, rng) for _ in range(20000)]
    assert statistics.fmean(xs) == pytest.approx(0.0, abs=0.15)
    assert statistics.pstdev(xs) == pytest.approx(sigma, rel=0.08)


def test_larger_scale_means_more_spread():
    def spread(scale):
        rng = KeyedRandom(b"spread")
        return statistics.pstdev([laplace(scale, rng) for _ in range(4000)])

    assert spread(10.0) > spread(1.0)


def test_discrete_laplace_returns_integers():
    rng = KeyedRandom(b"discrete")
    xs = [discrete_laplace(5.0, rng) for _ in range(200)]
    assert all(isinstance(x, int) for x in xs)
    # It must actually move, otherwise "integer noise" is just no noise.
    assert len(set(xs)) > 1


def test_clamp_bounds_both_ends_and_passes_through_the_middle():
    assert clamp(-5.0, 0.0, 10.0) == 0.0
    assert clamp(50.0, 0.0, 10.0) == 10.0
    assert clamp(4.0, 0.0, 10.0) == 4.0


def test_clamp_treats_none_as_unbounded():
    assert clamp(-5.0, None, 10.0) == -5.0
    assert clamp(50.0, 0.0, None) == 50.0
    assert clamp(1e9, None, None) == 1e9
