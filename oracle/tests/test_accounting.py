"""
zCDP accounting identities. `SPEC.md` §4.1.

These tests check the *definitions*, not remembered outputs: if the formulas are
right, the round trips below close. The golden-value tests at the bottom pin the
Rust implementation and this one to identical numbers.
"""
from __future__ import annotations

import math

import pytest

from privguard_oracle.accounting import (
    eps0_from_rho,
    eps_from_rho,
    grouped_l2_sensitivity,
    laplace_scale_from_rho,
    partition_threshold,
    sigma_from_rho,
)


@pytest.mark.parametrize("delta2,rho", [(1.0, 0.05), (12.5, 0.5), (0.1, 2.0)])
def test_gaussian_sigma_inverts_the_rho_definition(delta2, rho):
    # rho = D2^2 / (2 sigma^2) must hold for the sigma we hand back.
    sigma = sigma_from_rho(delta2, rho)
    assert delta2**2 / (2.0 * sigma**2) == pytest.approx(rho, rel=1e-12)


def test_laplace_scale_inverts_eps0():
    delta1, rho = 5.0, 0.125
    assert eps0_from_rho(rho) == pytest.approx(0.5)  # sqrt(2*0.125)
    assert laplace_scale_from_rho(delta1, rho) == pytest.approx(delta1 / 0.5)


def test_noise_scales_inversely_with_budget():
    # Spending more budget must buy less noise.
    assert sigma_from_rho(1.0, 0.5) < sigma_from_rho(1.0, 0.1)
    assert laplace_scale_from_rho(1.0, 0.5) < laplace_scale_from_rho(1.0, 0.1)


def test_noise_scales_linearly_with_sensitivity():
    assert sigma_from_rho(2.0, 0.3) == pytest.approx(2.0 * sigma_from_rho(1.0, 0.3))
    assert laplace_scale_from_rho(2.0, 0.3) == pytest.approx(
        2.0 * laplace_scale_from_rho(1.0, 0.3)
    )


@pytest.mark.parametrize("rho", [0.0, -1.0])
def test_nonpositive_rho_demands_infinite_noise(rho):
    assert math.isinf(sigma_from_rho(1.0, rho))
    assert math.isinf(laplace_scale_from_rho(1.0, rho))
    # ...but costs nothing to report.
    assert eps_from_rho(rho, 1e-6) == 0.0
    assert eps0_from_rho(rho) == 0.0


@pytest.mark.parametrize("rho", [0.01, 0.05, 0.5, 5.0])
def test_eps_always_exceeds_rho(rho):
    # eps(delta) = rho + 2*sqrt(rho ln(1/delta)) >= rho for delta < 1, so the
    # reported epsilon can never flatter the actual spend.
    assert eps_from_rho(rho, 1e-6) > rho


def test_eps_grows_with_rho_and_with_a_stronger_delta():
    assert eps_from_rho(0.10, 1e-6) > eps_from_rho(0.05, 1e-6)
    # A smaller delta is a stronger promise, so it reports a larger epsilon.
    assert eps_from_rho(0.05, 1e-9) > eps_from_rho(0.05, 1e-6)


def test_grouped_l2_uses_sqrt_of_the_group_bound():
    # sqrt(G), not G — the reason grouped queries use the Gaussian.
    assert grouped_l2_sensitivity(9, 2.0) == pytest.approx(6.0)
    assert grouped_l2_sensitivity(1, 3.0) == pytest.approx(3.0)


def test_partition_threshold_is_finite_and_above_one():
    tau = partition_threshold(0.01, 1e-2)
    assert math.isfinite(tau) and tau > 1.0


def test_a_mechanism_delta_here_suppresses_everything():
    # Regression guard for a real bug: passing the mechanism delta (1e-9)
    # instead of the partition delta (1e-2) drives tau so high every group is
    # dropped. It must visibly explode rather than quietly "work".
    sane = partition_threshold(0.01, 1e-2)
    wrong = partition_threshold(0.01, 1e-9)
    assert wrong > 4.0 * sane


def test_zero_partition_budget_suppresses_everything():
    assert math.isinf(partition_threshold(0.0, 1e-2))


# ------------------------------------------------------------------ goldens
# These exact numbers are asserted by the Rust implementation too
# (engine/crates/privguard-privacy). They are the contract between the two, so
# a change here without a matching change there is a differential-test failure
# waiting to happen in Phase 3.8.
def test_golden_values_shared_with_the_rust_engine():
    assert sigma_from_rho(5.0, 0.05) == pytest.approx(15.811388300841896, rel=1e-12)
    assert laplace_scale_from_rho(5.0, 0.05) == pytest.approx(15.811388300841896, rel=1e-12)
    assert eps_from_rho(0.05, 1e-6) == pytest.approx(1.71225813626911, rel=1e-12)
    assert eps0_from_rho(0.05) == pytest.approx(0.31622776601683794, rel=1e-12)
    assert partition_threshold(0.01, 1e-2) == pytest.approx(28.6621799529602, rel=1e-12)


def test_sigma_and_laplace_scale_coincide_by_construction():
    # Both are D / sqrt(2 rho); they differ only in which sensitivity norm the
    # caller feeds in (L2 vs L1). Worth pinning so a future "optimisation"
    # doesn't silently change one.
    assert sigma_from_rho(3.0, 0.2) == pytest.approx(laplace_scale_from_rho(3.0, 0.2))
