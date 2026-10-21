//! Role-based column access, enforced before a query is planned. `IMPLEMENTATION_PLAN.md` 4.1.
//!
//! The binder already refuses `Denied` columns outright. This is the second gate: a principal
//! may reference a column only if one of their roles holds a grant on it
//! (`sys_column_grant`). Referencing means anywhere in the query: the aggregated column, the
//! `GROUP BY` column, or a `WHERE` predicate; filtering on a column leaks as much as selecting
//! it.

use privguard_core::{Error, Result};
use privguard_sql::BoundQuery;
use std::collections::BTreeSet;

/// `(role, table, column)` grants.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Grants {
    set: BTreeSet<(String, String, String)>,
}

impl Grants {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn grant(&mut self, role: &str, table: &str, column: &str) {
        self.set
            .insert((role.to_string(), table.to_string(), column.to_string()));
    }

    pub fn revoke(&mut self, role: &str, table: &str, column: &str) -> bool {
        self.set
            .remove(&(role.to_string(), table.to_string(), column.to_string()))
    }

    pub fn iter(&self) -> impl Iterator<Item = &(String, String, String)> {
        self.set.iter()
    }

    pub fn allows(&self, roles: &[String], table: &str, column: &str) -> bool {
        roles.iter().any(|r| {
            self.set
                .contains(&(r.clone(), table.to_string(), column.to_string()))
        })
    }

    fn any_on_table(&self, roles: &[String], table: &str) -> bool {
        self.set
            .iter()
            .any(|(r, t, _)| t == table && roles.contains(r))
    }
}

/// Every column a query touches, sorted and de-duplicated.
pub fn referenced_columns(q: &BoundQuery) -> Vec<String> {
    let mut cols: BTreeSet<String> = q.filters.iter().map(|f| f.column.clone()).collect();
    cols.extend(q.column.iter().cloned());
    cols.extend(q.group_by.iter().cloned());
    cols.into_iter().collect()
}

/// Refuse the query unless the principal's roles cover every column it references.
pub fn check(q: &BoundQuery, roles: &[String], grants: &Grants) -> Result<()> {
    if !grants.any_on_table(roles, &q.table) {
        return Err(Error::policy(format!(
            "no role of this principal has access to {}",
            q.table
        )));
    }
    for col in referenced_columns(q) {
        if !grants.allows(roles, &q.table, &col) {
            return Err(Error::policy(format!(
                "no role of this principal may reference {}.{col}",
                q.table
            )));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use privguard_sql::parse;

    fn roles(r: &[&str]) -> Vec<String> {
        r.iter().map(|s| s.to_string()).collect()
    }

    fn analyst_grants() -> Grants {
        let mut g = Grants::new();
        for c in ["dept_id", "total_charge", "admission_type"] {
            g.grant("dp_analyst", "visits", c);
        }
        g
    }

    #[test]
    fn referenced_columns_cover_aggregate_group_and_filters() {
        let q =
            parse("SELECT SUM(total_charge) FROM visits WHERE dept_id = 2 AND total_charge > 5")
                .unwrap();
        assert_eq!(referenced_columns(&q), ["dept_id", "total_charge"]);
        let q = parse("SELECT COUNT(*) FROM visits GROUP BY admission_type").unwrap();
        assert_eq!(referenced_columns(&q), ["admission_type"]);
        assert!(referenced_columns(&parse("SELECT COUNT(*) FROM visits").unwrap()).is_empty());
    }

    #[test]
    fn a_covered_query_passes() {
        let q = parse("SELECT AVG(total_charge) FROM visits WHERE dept_id = 1").unwrap();
        check(&q, &roles(&["dp_analyst"]), &analyst_grants()).unwrap();
    }

    #[test]
    fn an_ungranted_column_is_refused_wherever_it_appears() {
        let g = analyst_grants();
        let r = roles(&["dp_analyst"]);
        for sql in [
            "SELECT COUNT(*) FROM visits WHERE admit_ts > '2024-01-01 00:00:00'",
            "SELECT COUNT(*) FROM visits WHERE total_charge > 1 AND discharge_ts < '2025-01-01'",
        ] {
            let err = check(&parse(sql).unwrap(), &r, &g).unwrap_err();
            assert_eq!(err.sqlstate().code(), "42501", "{sql}");
            assert!(err.to_string().contains("visits."), "{err}");
        }
    }

    #[test]
    fn roles_without_any_grant_cannot_query_the_table() {
        let q = parse("SELECT COUNT(*) FROM visits").unwrap();
        assert!(check(&q, &roles(&["clinician"]), &analyst_grants()).is_err());
        assert!(check(&q, &[], &analyst_grants()).is_err());
        check(&q, &roles(&["dp_analyst"]), &analyst_grants()).unwrap();
    }

    #[test]
    fn grants_from_any_of_the_principals_roles_combine() {
        let mut g = analyst_grants();
        g.grant("auditor", "visits", "admit_ts");
        let q = parse(
            "SELECT COUNT(*) FROM visits WHERE admit_ts > '2024-01-01 00:00:00' AND dept_id = 1",
        )
        .unwrap();
        assert!(check(&q, &roles(&["dp_analyst"]), &g).is_err());
        check(&q, &roles(&["dp_analyst", "auditor"]), &g).unwrap();
    }

    #[test]
    fn revoking_removes_access() {
        let mut g = analyst_grants();
        assert!(g.revoke("dp_analyst", "visits", "dept_id"));
        assert!(!g.revoke("dp_analyst", "visits", "dept_id"));
        let q = parse("SELECT COUNT(*) FROM visits WHERE dept_id = 1").unwrap();
        assert!(check(&q, &roles(&["dp_analyst"]), &g).is_err());
    }
}
