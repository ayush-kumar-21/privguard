//! The self-hosted catalog. `SPEC.md` §7.
//!
//! The catalog lives in the engine's own heap files, which means bootstrapping
//! has a chicken-and-egg problem: reading `sys_table` requires knowing the
//! shape of `sys_table`. It is resolved the way real systems do it — the
//! `sys_*` schemas are hard-coded here, and everything else is read from disk.
//!
//! Landed in Phase 2.5 (`IMPLEMENTATION_PLAN.md`).

pub mod catalog;
pub mod loader;
pub mod schema;

pub use catalog::{Catalog, ColumnDef, ColumnPrivacy, TableDef, TablePrivacy};
pub use loader::{create_safehealth_catalog, load_safehealth, LoadReport};

/// Names of the bootstrap catalog tables, in the order they are created.
///
/// Order matters: `sys_database` must exist before `sys_table` can reference
/// it, and the budget ledger's genesis entry needs its account to exist.
pub const BOOTSTRAP_TABLES: &[&str] = &[
    "sys_database",
    "sys_table",
    "sys_column",
    "sys_table_privacy",
    "sys_column_privacy",
    "sys_principal",
    "sys_role",
    "sys_principal_role",
    "sys_column_grant",
    "sys_budget_account",
    "sys_budget_ledger",
    "sys_query_fingerprint",
    "sys_query",
    "sys_noise_cache",
];

/// Whether an analyst may see a column at all. `SPEC.md` §7.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AccessMode {
    /// Readable in the clear.
    Allowed,
    /// May be aggregated over but never projected — the default for anything
    /// that feeds a DP aggregate.
    AggregateOnly,
    /// Not readable at all, e.g. `patients.mrn`.
    Denied,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bootstrap_order_puts_dependencies_first() {
        let pos = |name: &str| BOOTSTRAP_TABLES.iter().position(|t| *t == name).unwrap();
        assert!(pos("sys_database") < pos("sys_table"));
        assert!(pos("sys_table") < pos("sys_column"));
        assert!(pos("sys_table") < pos("sys_table_privacy"));
        assert!(pos("sys_column") < pos("sys_column_privacy"));
        assert!(pos("sys_principal") < pos("sys_budget_account"));
        assert!(pos("sys_budget_account") < pos("sys_budget_ledger"));
        assert!(pos("sys_query_fingerprint") < pos("sys_noise_cache"));
    }

    #[test]
    fn bootstrap_table_names_are_unique() {
        let mut seen = BOOTSTRAP_TABLES.to_vec();
        seen.sort_unstable();
        let before = seen.len();
        seen.dedup();
        assert_eq!(seen.len(), before);
    }
}
