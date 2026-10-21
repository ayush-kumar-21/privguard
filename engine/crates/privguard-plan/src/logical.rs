//! Logical planner. `IMPLEMENTATION_PLAN.md` 2.7.
//!
//! Turns a bound query into a small operator tree: `Scan -> [Filter] -> Aggregate`. It says
//! *what* is computed, not how; the physical DP pipeline (bounding, clamping, noise) is chosen
//! from this tree plus the query's privacy policy in Phase 3.

use privguard_sql::{Agg, BoundFilter, BoundQuery, CmpOp};

/// A node of the logical plan. Each non-leaf owns its single input.
#[derive(Debug, Clone, PartialEq)]
pub enum LogicalPlan {
    /// Read every row of `table`.
    Scan { table: String },
    /// Keep rows satisfying every predicate (conjunction). Never built with an empty list.
    Filter {
        input: Box<LogicalPlan>,
        predicates: Vec<BoundFilter>,
    },
    /// The query's single aggregate, optionally per `group_by` value.
    Aggregate {
        input: Box<LogicalPlan>,
        agg: Agg,
        /// `None` for `COUNT(*)`.
        column: Option<String>,
        group_by: Option<String>,
    },
}

/// Build the logical plan for `query`.
pub fn plan(query: &BoundQuery) -> LogicalPlan {
    let mut node = LogicalPlan::Scan {
        table: query.table.clone(),
    };
    if !query.filters.is_empty() {
        node = LogicalPlan::Filter {
            input: Box::new(node),
            predicates: query.filters.clone(),
        };
    }
    LogicalPlan::Aggregate {
        input: Box::new(node),
        agg: query.agg,
        column: query.column.clone(),
        group_by: query.group_by.clone(),
    }
}

fn op_symbol(op: CmpOp) -> &'static str {
    match op {
        CmpOp::Eq => "=",
        CmpOp::Ne => "<>",
        CmpOp::Gt => ">",
        CmpOp::Gte => ">=",
        CmpOp::Lt => "<",
        CmpOp::Lte => "<=",
    }
}

impl LogicalPlan {
    /// Operator names from the root down, e.g. `["Aggregate", "Filter", "Scan"]`.
    pub fn operators(&self) -> Vec<&'static str> {
        match self {
            LogicalPlan::Scan { .. } => vec!["Scan"],
            LogicalPlan::Filter { input, .. } => {
                let mut v = vec!["Filter"];
                v.extend(input.operators());
                v
            }
            LogicalPlan::Aggregate { input, .. } => {
                let mut v = vec!["Aggregate"];
                v.extend(input.operators());
                v
            }
        }
    }

    /// Indented multi-line rendering, root first (the shape the plan viewer displays).
    pub fn explain(&self) -> String {
        let mut out = String::new();
        self.render(0, &mut out);
        out
    }

    fn render(&self, depth: usize, out: &mut String) {
        let pad = "  ".repeat(depth);
        match self {
            LogicalPlan::Scan { table } => out.push_str(&format!("{pad}Scan {table}\n")),
            LogicalPlan::Filter { input, predicates } => {
                let preds: Vec<String> = predicates
                    .iter()
                    .map(|p| format!("{} {} {:?}", p.column, op_symbol(p.op), p.value))
                    .collect();
                out.push_str(&format!("{pad}Filter {}\n", preds.join(" AND ")));
                input.render(depth + 1, out);
            }
            LogicalPlan::Aggregate {
                input,
                agg,
                column,
                group_by,
            } => {
                let target = column.as_deref().unwrap_or("*");
                out.push_str(&format!("{pad}Aggregate {}({target})", agg.as_str()));
                if let Some(g) = group_by {
                    out.push_str(&format!(" GROUP BY {g}"));
                }
                out.push('\n');
                input.render(depth + 1, out);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use privguard_sql::parse;

    fn plan_of(sql: &str) -> LogicalPlan {
        plan(&parse(sql).unwrap())
    }

    #[test]
    fn count_star_is_aggregate_over_scan() {
        let p = plan_of("SELECT COUNT(*) FROM visits");
        assert_eq!(p.operators(), ["Aggregate", "Scan"]);
        assert_eq!(p.explain(), "Aggregate COUNT(*)\n  Scan visits\n");
    }

    #[test]
    fn where_clause_adds_one_filter_with_every_predicate() {
        let p = plan_of(
            "SELECT SUM(total_charge) FROM visits WHERE dept_id = 2 AND total_charge > 500",
        );
        assert_eq!(p.operators(), ["Aggregate", "Filter", "Scan"]);
        let LogicalPlan::Aggregate { input, .. } = &p else {
            panic!("root must be Aggregate")
        };
        let LogicalPlan::Filter { predicates, .. } = &**input else {
            panic!("expected Filter")
        };
        assert_eq!(predicates.len(), 2);
        assert!(p
            .explain()
            .contains("Filter dept_id = I64(2) AND total_charge > "));
    }

    #[test]
    fn group_by_is_carried_on_the_aggregate() {
        let p = plan_of("SELECT COUNT(*) FROM visits GROUP BY admission_type");
        let LogicalPlan::Aggregate {
            group_by, column, ..
        } = &p
        else {
            panic!("root must be Aggregate")
        };
        assert_eq!(group_by.as_deref(), Some("admission_type"));
        assert_eq!(*column, None);
        assert!(p
            .explain()
            .starts_with("Aggregate COUNT(*) GROUP BY admission_type\n"));
    }

    #[test]
    fn avg_names_its_column() {
        let p = plan_of("SELECT AVG(total_charge) FROM visits");
        assert!(p.explain().starts_with("Aggregate AVG(total_charge)\n"));
    }
}
