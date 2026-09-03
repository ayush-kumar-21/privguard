//! zCDP privacy accounting. `SPEC.md` §4.1.
//!
//! Composition under zero-concentrated DP is additive in ρ, which is the whole
//! reason the budget ledger can be a running sum rather than a composition
//! theorem evaluated over a query history.
//!
//! ```text
//! Gaussian   ρ = Δ₂² / (2σ²)        ⇒  σ  = Δ₂ / √(2ρ)
//! Laplace    ε₀-DP is (ε₀²/2)-zCDP  ⇒  ε₀ = √(2ρ),  b = Δ₁ / ε₀
//! Reporting  ε(δ) = ρ + 2√(ρ · ln(1/δ))
//! ```
//!
//! These functions are duplicated in `oracle/privguard_oracle/accounting.py`.
//! The duplication is deliberate — the oracle is an independent implementation
//! used to catch mistakes here — and the differential test in Phase 3.8 pins
//! the two together.

/// Gaussian scale σ for a target ρ and L2 sensitivity Δ₂.
///
/// A non-positive ρ buys no privacy budget and therefore requires infinite
/// noise; returning infinity rather than erroring keeps the caller's arithmetic
/// total, and any finite answer computed from it is `inf`, which is caught
/// downstream rather than silently released.
pub fn sigma_from_rho(delta2: f64, rho: f64) -> f64 {
    if rho <= 0.0 {
        return f64::INFINITY;
    }
    delta2 / (2.0 * rho).sqrt()
}

/// Laplace scale `b = Δ₁ / ε₀` where `ε₀ = √(2ρ)`.
pub fn laplace_scale_from_rho(delta1: f64, rho: f64) -> f64 {
    if rho <= 0.0 {
        return f64::INFINITY;
    }
    delta1 / (2.0 * rho).sqrt()
}

/// The (ε, δ) equivalent of a ρ spend, for human-readable reporting only.
///
/// Budgets are held and spent in ρ; this projection exists because analysts and
/// reviewers think in ε. Never accumulate the result — ε does not compose
/// additively, which is precisely the trap zCDP avoids.
pub fn eps_from_rho(rho: f64, delta: f64) -> f64 {
    if rho <= 0.0 {
        return 0.0;
    }
    rho + 2.0 * (rho * (1.0 / delta).ln()).sqrt()
}

/// ε₀ of the pure-DP mechanism underlying a ρ-zCDP Laplace spend.
pub fn eps0_from_rho(rho: f64) -> f64 {
    if rho <= 0.0 {
        return 0.0;
    }
    (2.0 * rho).sqrt()
}

/// L2 sensitivity of a grouped aggregate: `√G · Δ₁_per_group`. `SPEC.md` §4.3.
///
/// The `√G` rather than `G` is the point of using the Gaussian for grouped
/// queries — an L0 bound of G groups costs `√G` in L2 but a full `G` in L1.
pub fn grouped_l2_sensitivity(max_groups: u32, per_group_delta1: f64) -> f64 {
    (max_groups as f64).sqrt() * per_group_delta1
}

/// Partition-selection threshold τ. `SPEC.md` §4.5.
///
/// A group is released iff `noisy_distinct_units > τ`. `delta_partition` is a
/// per-partition knob chosen from the candidate-partition count — passing the
/// mechanism's own δ here suppresses every group, which is a bug that has
/// bitten this codebase before.
pub fn partition_threshold(rho_partition: f64, delta_partition: f64) -> f64 {
    let eps_part = eps0_from_rho(rho_partition);
    if eps_part <= 0.0 {
        return f64::INFINITY;
    }
    let scale = 1.0 / eps_part;
    1.0 + scale * (1.0 / (2.0 * delta_partition)).ln()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tolerance for float identities. Generous enough for `sqrt`/`ln` round
    /// trips, tight enough that a wrong formula fails.
    const EPS: f64 = 1e-12;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() <= EPS * a.abs().max(b.abs()).max(1.0)
    }

    #[test]
    fn gaussian_sigma_inverts_the_rho_definition() {
        // ρ = Δ₂² / (2σ²) must hold for the σ we hand back.
        for &(delta2, rho) in &[(1.0, 0.05), (12.5, 0.5), (0.1, 2.0)] {
            let sigma = sigma_from_rho(delta2, rho);
            let round_trip = delta2 * delta2 / (2.0 * sigma * sigma);
            assert!(
                close(round_trip, rho),
                "σ={sigma} gave ρ={round_trip}, want {rho}"
            );
        }
    }

    #[test]
    fn laplace_scale_inverts_eps0() {
        // b = Δ₁/ε₀ and ε₀ = √(2ρ).
        let (delta1, rho) = (5.0, 0.125);
        let b = laplace_scale_from_rho(delta1, rho);
        assert!(close(b, delta1 / eps0_from_rho(rho)));
        assert!(close(eps0_from_rho(rho), 0.5)); // √(2·0.125) = 0.5
    }

    #[test]
    fn noise_scales_inversely_with_budget() {
        // Spending more budget must buy less noise, for both mechanisms.
        assert!(sigma_from_rho(1.0, 0.5) < sigma_from_rho(1.0, 0.1));
        assert!(laplace_scale_from_rho(1.0, 0.5) < laplace_scale_from_rho(1.0, 0.1));
    }

    #[test]
    fn noise_scales_linearly_with_sensitivity() {
        // Doubling sensitivity doubles the scale — the calibration is linear.
        assert!(close(
            sigma_from_rho(2.0, 0.3),
            2.0 * sigma_from_rho(1.0, 0.3)
        ));
        assert!(close(
            laplace_scale_from_rho(2.0, 0.3),
            2.0 * laplace_scale_from_rho(1.0, 0.3)
        ));
    }

    #[test]
    fn nonpositive_rho_demands_infinite_noise() {
        assert!(sigma_from_rho(1.0, 0.0).is_infinite());
        assert!(sigma_from_rho(1.0, -1.0).is_infinite());
        assert!(laplace_scale_from_rho(1.0, 0.0).is_infinite());
        // ...but costs nothing to report.
        assert_eq!(eps_from_rho(0.0, 1e-6), 0.0);
        assert_eq!(eps0_from_rho(0.0), 0.0);
    }

    #[test]
    fn eps_reporting_matches_the_closed_form() {
        let (rho, delta) = (0.05, 1e-6);
        let want = rho + 2.0 * (rho * (1.0f64 / delta).ln()).sqrt();
        assert!(close(eps_from_rho(rho, delta), want));
    }

    #[test]
    fn eps_grows_with_rho_and_shrinks_with_delta() {
        assert!(eps_from_rho(0.1, 1e-6) > eps_from_rho(0.05, 1e-6));
        // A smaller δ is a stronger promise, so it reports a larger ε.
        assert!(eps_from_rho(0.05, 1e-9) > eps_from_rho(0.05, 1e-6));
    }

    #[test]
    fn eps_exceeds_rho() {
        // ε(δ) = ρ + 2√(ρ ln(1/δ)) ≥ ρ whenever δ < 1, so reporting can never
        // flatter the spend.
        for &rho in &[0.01, 0.05, 0.5, 5.0] {
            assert!(eps_from_rho(rho, 1e-6) > rho);
        }
    }

    #[test]
    fn grouped_l2_uses_sqrt_of_group_bound() {
        // √G, not G — the reason grouped queries use the Gaussian.
        assert!(close(grouped_l2_sensitivity(9, 2.0), 6.0));
        assert!(close(grouped_l2_sensitivity(1, 3.0), 3.0));
    }

    #[test]
    fn partition_threshold_is_finite_and_above_one() {
        // τ = 1 + b·ln(1/(2δ)) with δ = 1e-2 ⇒ ln(50) ≈ 3.912 > 0, so τ > 1.
        let tau = partition_threshold(0.01, 1e-2);
        assert!(tau.is_finite() && tau > 1.0, "tau = {tau}");
    }

    #[test]
    fn a_tiny_delta_partition_suppresses_everything() {
        // Regression guard for a real bug: passing the mechanism δ (1e-9)
        // instead of the partition δ (1e-2) drives τ so high that every group
        // is dropped. The threshold must visibly explode, not quietly work.
        let sane = partition_threshold(0.01, 1e-2);
        let wrong = partition_threshold(0.01, 1e-9);
        assert!(wrong > 4.0 * sane, "sane={sane}, wrong={wrong}");
    }

    #[test]
    fn zero_partition_budget_suppresses_everything() {
        assert!(partition_threshold(0.0, 1e-2).is_infinite());
    }

    /// Golden values shared with the Python oracle.
    ///
    /// `oracle/tests/test_accounting.py::test_golden_values_shared_with_the_rust_engine`
    /// asserts these same constants. They are the contract between the two
    /// implementations: changing one side without the other is a Phase 3.8
    /// differential-test failure waiting to happen.
    #[test]
    fn golden_values_shared_with_the_python_oracle() {
        assert!(close(sigma_from_rho(5.0, 0.05), 15.811388300841896));
        assert!(close(laplace_scale_from_rho(5.0, 0.05), 15.811388300841896));
        assert!(close(eps_from_rho(0.05, 1e-6), 1.71225813626911));
        assert!(close(eps0_from_rho(0.05), 0.31622776601683794));
        assert!(close(partition_threshold(0.01, 1e-2), 28.6621799529602));
    }
}
