"""
Noise mechanisms and the keyed stream. `SPEC.md` §4.4.

The determinism tests here are the load-bearing ones: they are what makes the
noise cache sound, and therefore what defeats the repeat-and-average attack.
"""
from __future__ import annotations

import statistics

import pytest

from privguard_oracle.mechanisms import (
    GRID,
    KeyedRandom,
    clamp,
    derive_seed,
    dgauss,
    dlap,
    noisy_count,
    noisy_gaussian,
    noisy_laplace,
    partition_cells,
    partition_cutoff,
)

SECRET = b"test-secret"
AST = bytes(range(32))


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


def test_clamp_bounds_both_ends_and_passes_through_the_middle():
    assert clamp(-5.0, 0.0, 10.0) == 0.0
    assert clamp(50.0, 0.0, 10.0) == 10.0
    assert clamp(4.0, 0.0, 10.0) == 4.0


def test_clamp_treats_none_as_unbounded():
    assert clamp(-5.0, None, 10.0) == -5.0
    assert clamp(50.0, 0.0, None) == 50.0
    assert clamp(1e9, None, None) == 1e9


# --- the keyed stream --------------------------------------------------------
def test_the_stream_is_deterministic_and_seed_dependent():
    a = [KeyedRandom(b"seed").below(10**9) for _ in range(5)]
    assert a == [KeyedRandom(b"seed").below(10**9) for _ in range(5)]
    assert KeyedRandom(b"seed-a").below(2**60) != KeyedRandom(b"seed-b").below(2**60)


def test_below_is_in_range_and_unbiased():
    rng = KeyedRandom(b"below")
    assert all(0 <= rng.below(7) < 7 for _ in range(500))
    counts = [0] * 3
    for _ in range(6000):
        counts[rng.below(3)] += 1
    assert all(abs(c - 2000) < 200 for c in counts)
    assert rng.below(1) == 0
    with pytest.raises(ValueError):
        rng.below(0)


# --- exactness of the samplers ----------------------------------------------
def _dlap_pmf(t: int, z: int) -> float:
    import math

    c = (1 - math.exp(-1 / t)) / (1 + math.exp(-1 / t))
    return c * math.exp(-abs(z) / t)


def test_dlap_matches_its_exact_pmf():
    t, n = 3, 40000
    rng = KeyedRandom(b"dlap-pmf")
    draws = [dlap(t, rng) for _ in range(n)]
    assert all(isinstance(z, int) for z in draws)
    for z in range(-4, 5):
        got = draws.count(z) / n
        want = _dlap_pmf(t, z)
        # 5 sigma of a binomial proportion
        assert abs(got - want) < 5 * (want * (1 - want) / n) ** 0.5, (z, got, want)


def test_dgauss_matches_its_exact_pmf():
    import math

    var, n = 9, 40000
    rng = KeyedRandom(b"dgauss-pmf")
    draws = [dgauss(var, rng) for _ in range(n)]
    norm = sum(math.exp(-z * z / (2 * var)) for z in range(-60, 61))
    for z in range(-6, 7):
        want = math.exp(-z * z / (2 * var)) / norm
        got = draws.count(z) / n
        assert abs(got - want) < 5 * (want * (1 - want) / n) ** 0.5, (z, got, want)


def test_samplers_are_deterministic_per_seed():
    assert [dlap(7, KeyedRandom(b"x")) for _ in range(3)] == [dlap(7, KeyedRandom(b"x")) for _ in range(3)]
    r1, r2 = KeyedRandom(b"y"), KeyedRandom(b"y")
    assert [dgauss(20, r1) for _ in range(20)] == [dgauss(20, r2) for _ in range(20)]


def test_sampler_arguments_are_validated():
    rng = KeyedRandom(b"v")
    for bad in (0, -3):
        with pytest.raises(ValueError):
            dlap(bad, rng)
        with pytest.raises(ValueError):
            dgauss(bad, rng)


# --- Mironov (2012): the released value must not depend on the data's float bits
def test_released_values_lie_on_a_grid_independent_of_the_true_value():
    """With float noise, the set of reachable outputs differs between neighbouring inputs.
    Here every output is `integer * step`, whatever the input, so the sets coincide."""
    unit, rho = 5000.0, 0.3
    step = unit / GRID
    for i in range(200):
        for value in (0.1, 0.1 + 2.0**-40, 1234.5678, 1234.5678 + 1e-9):
            out = noisy_laplace(value, unit, rho, KeyedRandom(b"m%d" % i))
            assert round(out / step) * step == out
            out = noisy_gaussian(value, unit, 3, rho, KeyedRandom(b"g%d" % i))
            assert round(out / step) * step == out


def test_noise_is_independent_of_the_value_so_neighbours_share_an_output_distribution():
    """Same seed, inputs one cell apart: outputs differ by exactly that cell."""
    unit, rho = 100.0, 0.5
    step = unit / GRID
    a = noisy_laplace(10 * step, unit, rho, KeyedRandom(b"n"))
    b = noisy_laplace(11 * step, unit, rho, KeyedRandom(b"n"))
    assert round((b - a) / step) == 1


def test_noisy_count_is_an_integer_and_centred():
    outs = [noisy_count(200, 5.0, 0.5, KeyedRandom(b"c%d" % i)) for i in range(400)]
    assert all(isinstance(o, int) for o in outs)
    assert abs(statistics.mean(outs) - 200) < 3


def test_rounding_ties_go_up_like_the_rust_engine():
    from privguard_oracle.mechanisms import _cells

    # Python's `round` is banker's; the engine uses floor(x + 0.5), so the oracle must too.
    assert _cells(0.5, 1.0) == 1 and _cells(-0.5, 1.0) == 0 and _cells(1.5, 1.0) == 2


def test_parameters_round_toward_more_noise():
    from privguard_oracle.mechanisms import _gaussian_variance, _laplace_scale

    for rho in (0.001, 0.05, 0.3, 2.0, 4.0):
        t = _laplace_scale(rho, GRID + 1)
        assert (GRID + 1) / t <= (2 * rho) ** 0.5 + 1e-12  # eps' <= sqrt(2 rho)
        v = _gaussian_variance(rho, 3 * (GRID + 1) ** 2)
        assert 3 * (GRID + 1) ** 2 / (2 * v) <= rho + 1e-12  # zCDP rho' <= rho


@pytest.mark.parametrize("rho", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_rho_is_refused(rho):
    with pytest.raises(ValueError):
        noisy_laplace(1.0, 10.0, rho, KeyedRandom(b"r"))
    with pytest.raises(ValueError):
        noisy_gaussian(1.0, 10.0, 1, rho, KeyedRandom(b"r"))


def test_a_rho_too_small_for_exact_sampling_is_refused():
    with pytest.raises(ValueError, match="too small"):
        noisy_gaussian(1.0, 10.0, 1, 1e-12, KeyedRandom(b"r"))


def test_partition_cutoff_keeps_the_single_unit_release_probability_under_delta():
    import math

    for rho, delta in ((0.01, 1e-2), (0.1, 1e-2), (0.5, 1e-3)):
        t, k = partition_cutoff(rho, delta)
        p = math.exp(-k / t) / (1 + math.exp(-1 / t))
        assert p <= delta * (1 + 1e-9)
        # ... and k is the smallest such integer, so utility is not wasted.
        p_less = math.exp(-(k - 1) / t) / (1 + math.exp(-1 / t))
        assert p_less > delta


def test_partition_selection_is_a_deterministic_function_of_units_and_seed():
    t, k = partition_cutoff(0.1, 1e-2)
    a = [partition_cells(40, t, KeyedRandom(b"p%d" % i)) >= GRID + k for i in range(20)]
    b = [partition_cells(40, t, KeyedRandom(b"p%d" % i)) >= GRID + k for i in range(20)]
    assert a == b and all(a)  # 40 units is far above the cutoff
