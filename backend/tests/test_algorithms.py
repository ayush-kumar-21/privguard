"""The tighter budget-to-epsilon conversion and the centred mean (AVG).

Both keep the guarantee and only make the same privacy cost buy more: a smaller reported epsilon,
and less noise on AVG. These tests pin the properties that make that safe.
"""
import math

import pytest

from privacy import accounting
from privacy.mechanisms import KeyedRandom
from privacy.service import Policy, _execute_scalar, _noisy
from privacy import mechanisms
from privacy.sql import parse

GRID = [(r, d) for r in (1e-4, 0.01, 0.05, 0.5, 2.0, 5.0, 30.0, 200.0) for d in (1e-3, 1e-6, 1e-9)]


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def exact_gaussian_eps(rho, delta):
    """Exact epsilon of a Gaussian mechanism with this rho (Balle & Wang's analytic curve).

    A rho-zCDP guarantee has to cover this mechanism, so no valid conversion may report less."""
    mu = math.sqrt(2.0 * rho)
    lo, hi = 0.0, accounting.eps_from_rho_classic(rho, delta)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        d = _phi(-mid / mu + mu / 2.0) - math.exp(mid) * _phi(-mid / mu - mu / 2.0)
        lo, hi = (mid, hi) if d > delta else (lo, mid)
    return hi


# --- the conversion ---------------------------------------------------------------
@pytest.mark.parametrize("rho, delta", GRID)
def test_never_above_the_classic_bound(rho, delta):
    assert accounting.eps_from_rho(rho, delta) <= accounting.eps_from_rho_classic(rho, delta) + 1e-12


@pytest.mark.parametrize("rho, delta", GRID)
def test_never_below_what_an_exact_gaussian_needs(rho, delta):
    """The validity check: reporting less than a real rho-zCDP mechanism achieves would be wrong."""
    floor = exact_gaussian_eps(rho, delta)
    assert accounting.eps_from_rho(rho, delta) >= floor * (1 - 1e-6), (rho, delta)


@pytest.mark.parametrize("rho, delta", [(0.05, 1e-6), (0.5, 1e-6), (5.0, 1e-6)])
def test_it_is_meaningfully_tighter(rho, delta):
    assert accounting.eps_from_rho(rho, delta) < 0.97 * accounting.eps_from_rho_classic(rho, delta)


def test_pinned_values():
    assert accounting.eps_from_rho(0.05, 1e-6) == pytest.approx(1.4716, abs=2e-3)
    assert accounting.eps_from_rho(0.5, 1e-6) == pytest.approx(5.2215, abs=2e-3)
    assert accounting.eps_from_rho(5.0, 1e-6) == pytest.approx(20.552, abs=5e-3)


def test_monotone_in_rho_and_in_delta():
    rhos = [0.001, 0.01, 0.1, 1.0, 10.0]
    eps = [accounting.eps_from_rho(r, 1e-6) for r in rhos]
    assert eps == sorted(eps) and len(set(eps)) == len(eps)
    by_delta = [accounting.eps_from_rho(0.5, d) for d in (1e-3, 1e-6, 1e-9, 1e-12)]
    assert by_delta == sorted(by_delta)  # a smaller delta costs more epsilon


def test_zero_and_negative_spend_are_zero():
    assert accounting.eps_from_rho(0.0, 1e-6) == 0.0 and accounting.eps_from_rho(-1.0, 1e-6) == 0.0


# --- the centred mean --------------------------------------------------------------
def test_policy_centre_and_half_range():
    p = Policy(5, 3, "laplace", 0.0, 1000.0)
    assert (p.center, p.half_range, p.clamp_magnitude) == (500.0, 500.0, 1000.0)
    q = Policy(2, 1, "laplace", -50.0, 400.0)
    assert (q.center, q.half_range) == (175.0, 225.0)


@pytest.fixture
def avg_world(visits):
    return parse("SELECT AVG(total_charge) FROM visits"), Policy(5, 3, "laplace", 0.0, 1000.0)


def _old_avg(spec, policy, rho, seed):
    """The previous algorithm (plain sum, sensitivity C*M), for comparison."""
    from privacy.service import _true_aggregate
    agg = _true_aggregate(spec, policy)
    C, M = policy.max_rows, policy.clamp_magnitude
    rng = KeyedRandom(seed)
    s = _noisy(policy.mechanism, agg["total"], rho / 2, C * M, C * M, rng, grid=mechanisms.SUM_GRID)
    n = _noisy(policy.mechanism, agg["n"], rho / 2, C, C, rng)
    return s / max(n, 1.0)


def test_the_centred_mean_is_accurate_at_a_generous_budget(avg_world):
    spec, policy = avg_world
    rows, sens, _ = _execute_scalar(spec, policy, 5000.0, KeyedRandom(b"seed-1"))
    assert rows[0]["value"] == pytest.approx(100.0, abs=1.0)  # every visit costs 100.0
    assert sens == 5 * 500.0  # C * half-range, not C * M


def test_it_is_more_accurate_than_the_old_algorithm_at_the_same_budget(avg_world):
    spec, policy = avg_world
    truth, rho = 100.0, 2.0
    new_err, old_err = [], []
    for i in range(300):
        seed = f"s{i}".encode()
        new_err.append((_execute_scalar(spec, policy, rho, KeyedRandom(seed))[0][0]["value"] - truth) ** 2)
        old_err.append((_old_avg(spec, policy, rho, seed) - truth) ** 2)
    rmse = lambda e: math.sqrt(sum(e) / len(e))
    assert rmse(new_err) < 0.9 * rmse(old_err), (rmse(new_err), rmse(old_err))


def test_a_constant_clamp_interval_reveals_nothing_about_the_sum(visits):
    """lo == hi: every clamped value equals the centre, so the centred sum is 0 for any data."""
    spec, policy = parse("SELECT AVG(total_charge) FROM visits"), Policy(5, 3, "laplace", 100.0, 100.0)
    rows, sens, _ = _execute_scalar(spec, policy, 5000.0, KeyedRandom(b"k"))
    assert sens == 0.0 and rows[0]["value"] == pytest.approx(100.0, abs=0.5)


def test_sum_and_count_are_unchanged(visits):
    policy = Policy(5, 3, "laplace", 0.0, 1000.0)
    _, sens_sum, _ = _execute_scalar(parse("SELECT SUM(total_charge) FROM visits"), policy, 1.0, KeyedRandom(b"a"))
    _, sens_cnt, _ = _execute_scalar(parse("SELECT COUNT(*) FROM visits"), policy, 1.0, KeyedRandom(b"a"))
    assert (sens_sum, sens_cnt) == (5 * 1000.0, 5.0)
