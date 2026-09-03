//! Parser and binder for the analyst SQL subset. `SPEC.md` §3.
//!
//! Analyst text is parsed to an AST and bound against the catalog. It is never
//! re-emitted as SQL against a downstream database — the engine executes the
//! plan itself, which is what removes SQL injection from the threat model
//! rather than merely mitigating it.
//!
//! Landed in Phase 2.6 (`IMPLEMENTATION_PLAN.md`).

use privguard_core::Result;

/// The aggregates the grammar admits. `SPEC.md` §3.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Agg {
    Count,
    Sum,
    Avg,
}

impl Agg {
    /// `COUNT` takes `*` only; `SUM`/`AVG` require a numeric column carrying
    /// clamp bounds. Encoded here so the binder and the planner cannot
    /// disagree about it.
    pub const fn requires_column(self) -> bool {
        matches!(self, Agg::Sum | Agg::Avg)
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Agg::Count => "COUNT",
            Agg::Sum => "SUM",
            Agg::Avg => "AVG",
        }
    }
}

/// Comparison operators admitted in `WHERE`. `SPEC.md` §3.
///
/// No `OR` and no `NOT`: both make predicate-overlap detection materially
/// harder, and overlap detection is what bounds correlated-query leakage.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CmpOp {
    Eq,
    Ne,
    Gt,
    Gte,
    Lt,
    Lte,
}

impl CmpOp {
    /// Canonical spelling used in the fingerprint. Stable across releases —
    /// changing one of these invalidates every cached answer.
    pub const fn canonical(self) -> &'static str {
        match self {
            CmpOp::Eq => "exact",
            CmpOp::Ne => "ne",
            CmpOp::Gt => "gt",
            CmpOp::Gte => "gte",
            CmpOp::Lt => "lt",
            CmpOp::Lte => "lte",
        }
    }
}

/// Placeholder until Phase 2.6 lands the real parser.
pub fn parse(_sql: &str) -> Result<()> {
    Err(privguard_core::Error::parse(
        "parser not implemented until Phase 2.6",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_sum_and_avg_need_a_column() {
        assert!(!Agg::Count.requires_column());
        assert!(Agg::Sum.requires_column());
        assert!(Agg::Avg.requires_column());
    }

    #[test]
    fn canonical_operator_spellings_are_distinct() {
        let ops = [
            CmpOp::Eq,
            CmpOp::Ne,
            CmpOp::Gt,
            CmpOp::Gte,
            CmpOp::Lt,
            CmpOp::Lte,
        ];
        let mut names: Vec<_> = ops.iter().map(|o| o.canonical()).collect();
        names.sort_unstable();
        let before = names.len();
        names.dedup();
        assert_eq!(names.len(), before, "two operators share a canonical name");
    }

    #[test]
    fn canonical_spellings_match_the_python_oracle() {
        // These strings go into the fingerprint hash, so they must match
        // oracle/privguard_oracle/sql.py exactly or the two implementations
        // will cache under different keys.
        assert_eq!(CmpOp::Eq.canonical(), "exact");
        assert_eq!(CmpOp::Ne.canonical(), "ne");
        assert_eq!(CmpOp::Gte.canonical(), "gte");
    }

    #[test]
    fn parser_is_honestly_unimplemented() {
        assert!(parse("SELECT COUNT(*) FROM visits").is_err());
    }
}
