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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mechanisms::partition_cutoff;

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
    fn partition_cutoff_is_positive() {
        let c = partition_cutoff(0.01, 1e-2).unwrap();
        assert!(c.scale >= 1 && c.cutoff >= 1);
    }

    #[test]
    fn a_tiny_delta_partition_suppresses_everything() {
        // Regression guard for a real bug: passing the mechanism δ (1e-9) instead of the
        // partition δ (1e-2) drives the cutoff so high that every group is dropped. It must
        // visibly explode, not quietly work.
        let sane = partition_cutoff(0.01, 1e-2).unwrap().cutoff;
        let wrong = partition_cutoff(0.01, 1e-9).unwrap().cutoff;
        assert!(wrong > 4 * sane, "sane={sane}, wrong={wrong}");
    }

    #[test]
    fn zero_partition_budget_is_refused() {
        assert!(partition_cutoff(0.0, 1e-2).is_err());
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
        let cut = |rho, delta| {
            let c = partition_cutoff(rho, delta).unwrap();
            (c.scale, c.cutoff)
        };
        assert_eq!(cut(0.01, 1e-2), (7241, 28328));
        assert_eq!(cut(0.1, 1e-2), (2290, 8960));
        assert_eq!(cut(0.5, 1e-3), (1024, 6365));
    }

    #[test]
    #[allow(clippy::approx_constant)]
    fn golden_fixture_zcdp_grid_matches_python_oracle() {
        let rho_vectors = [
            (0.02, 1.0713043539513865, 0.2, 44.73774684840182),
            (
                0.25,
                3.9669221888498383,
                0.7071067811865476,
                13.370902956130188,
            ),
            (0.5, 5.756521769756932, 1.0, 9.747549369680364),
            (0.75, 7.1878980788680416, 1.224744871391589, 8.1423441518405),
            (
                1.0,
                8.433844377699677,
                1.4142135623730951,
                7.185451478065094,
            ),
            (
                4.0,
                18.867688755399353,
                2.8284271247461903,
                4.092725739032547,
            ),
        ];
        for (rho, want_eps, want_eps0, _) in rho_vectors {
            assert!(close(eps_from_rho(rho, 1e-6), want_eps));
            assert!(close(eps0_from_rho(rho), want_eps0));
        }

        let scale_vectors = [
            (3u32, 5.0, 0.5, 8.660254037844386, 9.682458365518542, 5.0),
            (3, 5000.0, 0.5, 8660.254037844386, 9682.458365518542, 5000.0),
            (1, 800.0, 0.5, 800.0, 894.4271909999159, 800.0),
            (
                3,
                5000.0,
                1.0,
                8660.254037844386,
                6846.531968814576,
                3535.5339059327375,
            ),
            (3, 5.0, 0.02, 8.660254037844386, 48.4122918275927, 25.0),
        ];
        for (g, d1, rho, want_d2, want_sigma, want_b) in scale_vectors {
            let d2 = grouped_l2_sensitivity(g, d1);
            assert!(close(d2, want_d2));
            assert!(close(sigma_from_rho(d2, 0.8 * rho), want_sigma));
            assert!(close(laplace_scale_from_rho(d1, rho), want_b));
        }
    }
}
