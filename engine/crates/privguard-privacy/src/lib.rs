//! Privacy core: zCDP accounting, noise mechanisms, the physical DP operators
//! and the hash-chained budget ledger.
//!
//! This crate holds two of the blueprint's three core contributions — privacy
//! inside the executor, and budget as system state. The third, the
//! attack-resistant rewriter, lives in `privguard-plan` where it can see the
//! bound query.
//!
//! Phase 1 lands the accounting only; mechanisms and operators follow in
//! Phase 3 (see `IMPLEMENTATION_PLAN.md`).

pub mod accounting;
pub mod fingerprint;
pub mod ledger;
pub mod mechanisms;
pub mod operators;
pub mod policy;

pub use fingerprint::{CanonicalQuery, Literal, Predicate, QueryFingerprint};
pub use mechanisms::{
    clamp, derive_seed, discrete_laplace, gaussian, laplace, sha256, KeyedRandom,
};
pub use policy::{ColumnPolicy, Mechanism, PrivacyPolicy, TablePolicy};

/// Default privacy parameters, frozen in `SPEC.md` §4.
pub mod defaults {
    /// Budget charged for a query when the analyst does not ask for a specific
    /// spend.
    pub const RHO: f64 = 0.05;

    /// L∞ contribution bound C — rows per privacy unit.
    pub const MAX_ROWS_PER_UNIT: u32 = 5;

    /// L0 contribution bound G — groups per privacy unit.
    pub const MAX_GROUPS_PER_UNIT: u32 = 3;

    /// δ for DP partition selection. A per-partition knob chosen from the
    /// candidate-partition count — deliberately *not* the mechanism δ, which
    /// is orders of magnitude smaller and would suppress every group.
    pub const DELTA_PARTITION: f64 = 1e-2;

    /// Fraction of the query budget spent deciding *which* groups to release,
    /// with the remainder spent noising the survivors. `SPEC.md` §4.5.
    pub const PARTITION_BUDGET_FRACTION: f64 = 0.2;
}

// These defaults are frozen in `SPEC.md` §4, so they are checked at compile
// time rather than in a `#[test]`: a bad constant should fail the build, and a
// runtime `assert!` over a constant is folded away and proves nothing.

// Most of the budget must go to the answer, not to deciding which groups to
// release.
const _: () = assert!(defaults::PARTITION_BUDGET_FRACTION > 0.0);
const _: () = assert!(defaults::PARTITION_BUDGET_FRACTION < 0.5);

// Guards the §4.5 footgun: a mechanism δ here (1e-9 and smaller) drives the
// partition threshold so high that every group is suppressed.
const _: () = assert!(defaults::DELTA_PARTITION >= 1e-3);

// Contribution bounds have to actually bound something.
const _: () = assert!(defaults::MAX_ROWS_PER_UNIT >= 1);
const _: () = assert!(defaults::MAX_GROUPS_PER_UNIT >= 1);

// A default spend of zero would demand infinite noise on every query.
const _: () = assert!(defaults::RHO > 0.0);

#[cfg(test)]
mod golden_tests;
