//! Logical planning, the privacy rewriter, and the Volcano physical plan.
//!
//! This crate is where the blueprint's first core contribution lives: **privacy
//! inside the executor**. Noise is not added by a wrapper around the result —
//! it is produced by a physical operator sitting in the plan, so there is no
//! code path that reaches data and skips it.
//!
//! The physical pipeline is fixed (`SPEC.md` §4):
//!
//! ```text
//! Scan → Filter → ContributionBound → Clamp → [PartitionSelect] → DPAggregate
//! ```
//!
//! Landed in Phase 2.7–2.8 (non-private) and Phase 3.3–3.5 (DP operators and
//! the rewriter). See `IMPLEMENTATION_PLAN.md`.

pub mod build;
pub mod exec;
pub mod logical;

pub use logical::{plan, LogicalPlan};

/// Physical operators, in the order they appear in a plan.
///
/// The discriminant order is load-bearing: `plan_is_well_formed` uses it to
/// check that a plan never places, say, `Clamp` above `DPAggregate`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum PhysicalOp {
    /// Sequential scan of a heap file, or an index scan when a predicate is
    /// selective enough.
    Scan,
    /// Predicate evaluation.
    Filter,
    /// L∞ and L0 contribution bounding — at most C rows and G groups per
    /// privacy unit. `SPEC.md` §4.2.
    ContributionBound,
    /// Clamp values into the policy's `[lo, hi]`, which is what gives the
    /// aggregate a finite sensitivity at all.
    Clamp,
    /// DP partition selection — drop groups whose noisy unit count falls below
    /// τ. `SPEC.md` §4.5. Grouped queries only; runs before the aggregate so
    /// that only surviving groups are noised (`SPEC.md` §10, A1).
    PartitionSelect,
    /// The aggregate plus calibrated noise.
    DPAggregate,
}

/// Whether a sequence of operators forms a legal plan.
///
/// Two rules, both of which are privacy-critical rather than merely tidy:
/// bounding and clamping must precede aggregation (otherwise the sensitivity
/// the noise was calibrated against is wrong), and partition selection must
/// precede the noised aggregate (the survivors are decided first, then only they are noised).
pub fn plan_is_well_formed(ops: &[PhysicalOp]) -> bool {
    if ops.first() != Some(&PhysicalOp::Scan) {
        return false;
    }
    if !ops.windows(2).all(|w| w[0] < w[1]) {
        return false;
    }
    // Noise must exist wherever data does.
    ops.contains(&PhysicalOp::DPAggregate)
}

#[cfg(test)]
mod tests {
    use super::PhysicalOp::*;
    use super::*;

    #[test]
    fn the_canonical_scalar_plan_is_well_formed() {
        assert!(plan_is_well_formed(&[
            Scan,
            Filter,
            ContributionBound,
            Clamp,
            DPAggregate
        ]));
    }

    #[test]
    fn the_canonical_grouped_plan_is_well_formed() {
        assert!(plan_is_well_formed(&[
            Scan,
            Filter,
            ContributionBound,
            Clamp,
            PartitionSelect,
            DPAggregate
        ]));
    }

    #[test]
    fn clamping_after_aggregation_is_rejected() {
        // Clamping the aggregate rather than the inputs would leave the true
        // sensitivity unbounded while the noise pretended otherwise.
        assert!(!plan_is_well_formed(&[Scan, DPAggregate, Clamp]));
    }

    #[test]
    fn bounding_after_aggregation_is_rejected() {
        assert!(!plan_is_well_formed(&[
            Scan,
            DPAggregate,
            ContributionBound
        ]));
    }

    #[test]
    fn partition_selection_after_aggregation_is_rejected() {
        assert!(!plan_is_well_formed(&[Scan, DPAggregate, PartitionSelect]));
    }

    #[test]
    fn a_plan_without_noise_is_rejected() {
        // The whole point of putting privacy in the executor: a plan that
        // reaches data without a DPAggregate is not a plan we will run.
        assert!(!plan_is_well_formed(&[
            Scan,
            Filter,
            ContributionBound,
            Clamp
        ]));
    }

    #[test]
    fn a_plan_must_start_at_a_scan() {
        assert!(!plan_is_well_formed(&[Filter, DPAggregate]));
        assert!(!plan_is_well_formed(&[]));
    }
}

#[cfg(test)]
mod golden_tests;
