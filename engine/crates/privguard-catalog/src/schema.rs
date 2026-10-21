//! Hard-coded schemas of the `sys_*` tables. `SPEC.md` §7.
//!
//! The catalog cannot read its own schema from a catalog that does not exist yet, so these
//! are the bootstrap: everything else (user tables, columns, policies) is read from disk.

use privguard_core::ColumnType;
use privguard_core::ColumnType::{Bool, Text, Timestamp, F64, I32, I64};

pub type Columns = &'static [(&'static str, ColumnType)];

pub const SYS_DATABASE: Columns = &[("name", Text), ("default_delta", F64)];
pub const SYS_TABLE: Columns = &[
    ("table_id", I64),
    ("name", Text),
    ("is_fact", Bool),
    ("row_estimate", I64),
];
pub const SYS_COLUMN: Columns = &[
    ("table_id", I64),
    ("ordinal", I32),
    ("name", Text),
    ("col_type", Text),
    ("nullable", Bool),
    ("encrypted", Bool),
    ("filterable", Bool),
    ("groupable", Bool),
];
pub const SYS_TABLE_PRIVACY: Columns = &[
    ("table_id", I64),
    ("max_rows_per_unit", I32),
    ("max_groups_per_unit", I32),
];
pub const SYS_COLUMN_PRIVACY: Columns = &[
    ("table_id", I64),
    ("column", Text),
    ("mechanism", Text),
    ("lower_clamp", F64),
    ("upper_clamp", F64),
    ("access", Text),
];
pub const SYS_PRINCIPAL: Columns = &[
    ("principal_id", I64),
    ("name", Text),
    ("password_hash", Text),
];
pub const SYS_ROLE: Columns = &[("role_id", I64), ("name", Text)];
pub const SYS_PRINCIPAL_ROLE: Columns = &[("principal_id", I64), ("role_id", I64)];
pub const SYS_COLUMN_GRANT: Columns = &[("role_id", I64), ("table_id", I64), ("column", Text)];
pub const SYS_BUDGET_ACCOUNT: Columns = &[
    ("account_id", I64),
    ("principal_id", I64),
    ("rho_total", F64),
];
/// One row per ledger entry; `prev_hash` and `hash` are lowercase hex.
pub const SYS_BUDGET_LEDGER: Columns = &[
    ("seq", I64),
    ("account_id", I64),
    ("query_id", I64),
    ("kind", Text),
    ("rho", F64),
    ("mechanism", Text),
    ("sensitivity", F64),
    ("scale", F64),
    ("eps_equiv", F64),
    ("prev_hash", Text),
    ("hash", Text),
];
pub const SYS_QUERY_FINGERPRINT: Columns = &[("fingerprint", Text), ("canonical", Text)];
pub const SYS_QUERY: Columns = &[
    ("query_id", I64),
    ("principal_id", I64),
    ("fingerprint", Text),
    ("status", Text),
    ("rho", F64),
    ("ts", Timestamp),
    ("sql_text", Text),
];
pub const SYS_NOISE_CACHE: Columns = &[
    ("fingerprint", Text),
    ("principal_id", I64),
    ("result", Text),
    ("rho", F64),
];

/// Schema of a bootstrap table, or `None` if `name` is not one.
pub fn sys_schema(name: &str) -> Option<Columns> {
    Some(match name {
        "sys_database" => SYS_DATABASE,
        "sys_table" => SYS_TABLE,
        "sys_column" => SYS_COLUMN,
        "sys_table_privacy" => SYS_TABLE_PRIVACY,
        "sys_column_privacy" => SYS_COLUMN_PRIVACY,
        "sys_principal" => SYS_PRINCIPAL,
        "sys_role" => SYS_ROLE,
        "sys_principal_role" => SYS_PRINCIPAL_ROLE,
        "sys_column_grant" => SYS_COLUMN_GRANT,
        "sys_budget_account" => SYS_BUDGET_ACCOUNT,
        "sys_budget_ledger" => SYS_BUDGET_LEDGER,
        "sys_query_fingerprint" => SYS_QUERY_FINGERPRINT,
        "sys_query" => SYS_QUERY,
        "sys_noise_cache" => SYS_NOISE_CACHE,
        _ => return None,
    })
}

pub fn types_of(cols: Columns) -> Vec<ColumnType> {
    cols.iter().map(|(_, t)| *t).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::BOOTSTRAP_TABLES;

    #[test]
    fn every_bootstrap_table_has_a_schema() {
        for t in BOOTSTRAP_TABLES {
            assert!(sys_schema(t).is_some(), "{t} has no schema");
        }
        assert!(sys_schema("visits").is_none());
    }

    #[test]
    fn column_names_are_unique_within_a_table() {
        for t in BOOTSTRAP_TABLES {
            let mut names: Vec<_> = sys_schema(t).unwrap().iter().map(|c| c.0).collect();
            names.sort_unstable();
            let n = names.len();
            names.dedup();
            assert_eq!(names.len(), n, "{t}");
        }
    }
}
