//! Privacy policy types. Phase 0 freeze. `SPEC.md` §4.2, §4.3 and §7.
//!
//! Mirrors `TablePolicy` / `ColumnPolicy` in `oracle/privguard_oracle/spec.py`,
//! including the validation rules, so a policy the oracle rejects is rejected
//! here too.

use crate::defaults;
use privguard_core::{Error, Result};

/// Which noise mechanism a column's aggregates use. `SPEC.md` §4.4.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Mechanism {
    Laplace,
    Gaussian,
    DiscreteLaplace,
}

impl Mechanism {
    /// Spelling used in the catalog and in the golden fixtures.
    pub const fn as_str(self) -> &'static str {
        match self {
            Mechanism::Laplace => "laplace",
            Mechanism::Gaussian => "gaussian",
            Mechanism::DiscreteLaplace => "discrete_laplace",
        }
    }

    pub fn parse(s: &str) -> Result<Self> {
        match s {
            "laplace" => Ok(Mechanism::Laplace),
            "gaussian" => Ok(Mechanism::Gaussian),
            "discrete_laplace" => Ok(Mechanism::DiscreteLaplace),
            other => Err(Error::catalog(format!("unknown mechanism: {other}"))),
        }
    }
}

/// Contribution bounds for a fact table (`sys_table_privacy`). `SPEC.md` §4.2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TablePolicy {
    /// C, the L-inf bound: rows kept per privacy unit.
    pub max_rows_per_unit: u32,
    /// G, the L0 bound: groups a privacy unit may contribute to.
    pub max_groups_per_unit: u32,
}

impl TablePolicy {
    pub fn new(max_rows_per_unit: u32, max_groups_per_unit: u32) -> Result<Self> {
        if max_rows_per_unit < 1 {
            return Err(Error::policy("max_rows_per_unit (C) must be at least 1"));
        }
        if max_groups_per_unit < 1 {
            return Err(Error::policy("max_groups_per_unit (G) must be at least 1"));
        }
        Ok(TablePolicy {
            max_rows_per_unit,
            max_groups_per_unit,
        })
    }
}

impl Default for TablePolicy {
    /// `C = 5`, `G = 3` when a table carries no policy. `SPEC.md` §4.2.
    fn default() -> Self {
        TablePolicy {
            max_rows_per_unit: defaults::MAX_ROWS_PER_UNIT,
            max_groups_per_unit: defaults::MAX_GROUPS_PER_UNIT,
        }
    }
}

/// Mechanism and clamp bounds for an aggregatable column (`sys_column_privacy`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ColumnPolicy {
    pub mechanism: Mechanism,
    pub lower_clamp: f64,
    pub upper_clamp: f64,
}

impl ColumnPolicy {
    pub fn new(mechanism: Mechanism, lower_clamp: f64, upper_clamp: f64) -> Result<Self> {
        if !lower_clamp.is_finite() || !upper_clamp.is_finite() {
            return Err(Error::policy("clamp bounds must be finite"));
        }
        if lower_clamp > upper_clamp {
            return Err(Error::policy(format!(
                "clamp bounds inverted: [{lower_clamp}, {upper_clamp}]"
            )));
        }
        let p = ColumnPolicy {
            mechanism,
            lower_clamp,
            upper_clamp,
        };
        if p.magnitude() == 0.0 {
            // M = 0 would make SUM sensitivity zero, i.e. release the true sum.
            return Err(Error::policy(
                "clamp bounds [0, 0] give a zero-sensitivity policy",
            ));
        }
        Ok(p)
    }

    /// M = max(|lo|, |hi|), the per-row contribution bound. `SPEC.md` §4.3.
    pub fn magnitude(&self) -> f64 {
        self.lower_clamp.abs().max(self.upper_clamp.abs())
    }
}

/// Everything the planner needs to calibrate noise for one query: the table's
/// contribution bounds and, for `SUM`/`AVG`, the aggregated column's policy.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct PrivacyPolicy {
    pub table: TablePolicy,
    /// `None` for `COUNT(*)`; required for `SUM` and `AVG`.
    pub column: Option<ColumnPolicy>,
}

impl PrivacyPolicy {
    pub fn new(table: TablePolicy, column: Option<ColumnPolicy>) -> Self {
        PrivacyPolicy { table, column }
    }

    /// M for sensitivity arithmetic: the column's magnitude, or 1 with no column
    /// (the oracle's convention for `COUNT`).
    pub fn magnitude(&self) -> f64 {
        self.column.map_or(1.0, |c| c.magnitude())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_defaults_are_c5_g3() {
        let t = TablePolicy::default();
        assert_eq!((t.max_rows_per_unit, t.max_groups_per_unit), (5, 3));
    }

    #[test]
    fn table_bounds_must_be_positive() {
        assert!(TablePolicy::new(0, 3).is_err());
        assert!(TablePolicy::new(5, 0).is_err());
        assert_eq!(TablePolicy::new(2, 1).unwrap().max_rows_per_unit, 2);
    }

    #[test]
    fn magnitude_is_the_larger_absolute_bound() {
        let p = ColumnPolicy::new(Mechanism::Laplace, -50.0, 400.0).unwrap();
        assert_eq!(p.magnitude(), 400.0);
        let q = ColumnPolicy::new(Mechanism::Laplace, -500.0, 100.0).unwrap();
        assert_eq!(q.magnitude(), 500.0);
    }

    #[test]
    fn inverted_zero_and_non_finite_clamps_are_rejected() {
        assert!(ColumnPolicy::new(Mechanism::Laplace, 10.0, 1.0).is_err());
        assert!(ColumnPolicy::new(Mechanism::Laplace, 0.0, 0.0).is_err());
        assert!(ColumnPolicy::new(Mechanism::Laplace, f64::NEG_INFINITY, 1.0).is_err());
        assert!(ColumnPolicy::new(Mechanism::Laplace, 0.0, f64::NAN).is_err());
    }

    #[test]
    fn rejections_use_the_policy_error_class() {
        let e = ColumnPolicy::new(Mechanism::Laplace, 0.0, 0.0).unwrap_err();
        assert_eq!(e.sqlstate().code(), "42501");
    }

    #[test]
    fn mechanism_names_round_trip_and_match_the_fixtures() {
        for m in [
            Mechanism::Laplace,
            Mechanism::Gaussian,
            Mechanism::DiscreteLaplace,
        ] {
            assert_eq!(Mechanism::parse(m.as_str()).unwrap(), m);
        }
        assert_eq!(Mechanism::DiscreteLaplace.as_str(), "discrete_laplace");
        assert!(Mechanism::parse("exponential").is_err());
    }

    #[test]
    fn count_policy_has_unit_magnitude() {
        assert_eq!(PrivacyPolicy::default().magnitude(), 1.0);
        let col = ColumnPolicy::new(Mechanism::Laplace, 0.0, 1000.0).unwrap();
        assert_eq!(
            PrivacyPolicy::new(TablePolicy::default(), Some(col)).magnitude(),
            1000.0
        );
    }
}
