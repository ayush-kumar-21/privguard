//! Parser and binder for the analyst SQL subset. `SPEC.md` §3 (`IMPLEMENTATION_PLAN.md` 2.6).
//!
//! Analyst text is parsed to an AST via `sqlparser-rs` and bound against a catalog
//! (`CatalogLookup`, with `MockCatalog` used until the self-hosted catalog lands in
//! Phase 2.5). SQL text is never re-emitted against a downstream database.

use privguard_catalog::AccessMode;
use privguard_core::{ColumnType, Error, Result, Value};
use sqlparser::ast::{
    BinaryOperator, Expr, FunctionArg, FunctionArgExpr, FunctionArguments, GroupByExpr, Ident,
    ObjectName, Query, SelectItem, SetExpr, Statement, TableFactor, UnaryOperator,
    Value as SqlValue,
};
use sqlparser::dialect::PostgreSqlDialect;
use sqlparser::parser::Parser;
use std::collections::HashMap;

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

/// Metadata for a single column in the catalog.
#[derive(Debug, Clone, PartialEq)]
pub struct ColumnMeta {
    pub name: String,
    pub col_type: ColumnType,
    pub access: AccessMode,
    pub filterable: bool,
    pub groupable: bool,
    pub clamp_bounds: Option<(f64, f64)>,
}

/// Metadata for a table in the catalog.
#[derive(Debug, Clone, PartialEq)]
pub struct TableMeta {
    pub name: String,
    /// Only the privacy-unit fact table (`visits` in v1) may be aggregated over.
    pub is_fact_table: bool,
    pub max_rows_per_unit: u32,
    pub max_groups_per_unit: u32,
    pub columns: HashMap<String, ColumnMeta>,
}

/// Seam for resolving tables, columns, and privacy policies during binding.
pub trait CatalogLookup {
    fn table(&self, name: &str) -> Option<&TableMeta>;
}

/// In-memory mock catalog pre-populated with the `SPEC.md` §2.2 & §3 SafeHealth schema.
#[derive(Debug, Clone, Default)]
pub struct MockCatalog {
    tables: HashMap<String, TableMeta>,
}

impl MockCatalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert_table(&mut self, table: TableMeta) {
        self.tables.insert(table.name.to_ascii_lowercase(), table);
    }

    /// Standard SafeHealth catalog (`SPEC.md` §2.2 and §3).
    pub fn safehealth() -> Self {
        let mut cat = MockCatalog::new();

        let mut visit_cols = HashMap::new();
        let mut add_col = |name: &str,
                           col_type: ColumnType,
                           access: AccessMode,
                           filterable: bool,
                           groupable: bool,
                           clamp_bounds: Option<(f64, f64)>| {
            visit_cols.insert(
                name.to_string(),
                ColumnMeta {
                    name: name.to_string(),
                    col_type,
                    access,
                    filterable,
                    groupable,
                    clamp_bounds,
                },
            );
        };

        add_col(
            "visit_id",
            ColumnType::I64,
            AccessMode::AggregateOnly,
            false,
            false,
            None,
        );
        add_col(
            "patient_id",
            ColumnType::I64,
            AccessMode::Denied,
            false,
            false,
            None,
        );
        add_col(
            "dept_id",
            ColumnType::I64,
            AccessMode::Allowed,
            true,
            true,
            None,
        );
        add_col(
            "attending_staff_id",
            ColumnType::I64,
            AccessMode::AggregateOnly,
            false,
            false,
            None,
        );
        add_col(
            "admit_ts",
            ColumnType::Timestamp,
            AccessMode::Allowed,
            true,
            false,
            None,
        );
        add_col(
            "discharge_ts",
            ColumnType::Timestamp,
            AccessMode::Allowed,
            true,
            false,
            None,
        );
        add_col(
            "total_charge",
            ColumnType::F64,
            AccessMode::AggregateOnly,
            true,
            false,
            Some((0.0, 1000.0)),
        );
        add_col(
            "admission_type",
            ColumnType::Text,
            AccessMode::Allowed,
            true,
            true,
            None,
        );

        cat.insert_table(TableMeta {
            name: "visits".to_string(),
            is_fact_table: true,
            max_rows_per_unit: 5,
            max_groups_per_unit: 3,
            columns: visit_cols,
        });

        // Dimension table present in the catalog so `FROM patients` is a PolicyError
        // (`42501`) rather than an undefined-table CatalogError (`42P01`).
        let mut patient_cols = HashMap::new();
        patient_cols.insert(
            "patient_id".to_string(),
            ColumnMeta {
                name: "patient_id".to_string(),
                col_type: ColumnType::I64,
                access: AccessMode::Denied,
                filterable: false,
                groupable: false,
                clamp_bounds: None,
            },
        );
        patient_cols.insert(
            "mrn".to_string(),
            ColumnMeta {
                name: "mrn".to_string(),
                col_type: ColumnType::Text,
                access: AccessMode::Denied,
                filterable: false,
                groupable: false,
                clamp_bounds: None,
            },
        );
        cat.insert_table(TableMeta {
            name: "patients".to_string(),
            is_fact_table: false,
            max_rows_per_unit: 1,
            max_groups_per_unit: 1,
            columns: patient_cols,
        });

        cat
    }
}

impl CatalogLookup for MockCatalog {
    fn table(&self, name: &str) -> Option<&TableMeta> {
        self.tables.get(&name.to_ascii_lowercase())
    }
}

/// One bound conjunctive predicate in `WHERE`.
#[derive(Debug, Clone, PartialEq)]
pub struct BoundFilter {
    pub column: String,
    pub op: CmpOp,
    pub value: Value,
}

/// A parsed and catalog-bound query ready for logical planning.
#[derive(Debug, Clone, PartialEq)]
pub struct BoundQuery {
    pub agg: Agg,
    pub column: Option<String>,
    pub clamp_bounds: Option<(f64, f64)>,
    pub table: String,
    pub max_rows_per_unit: u32,
    pub max_groups_per_unit: u32,
    pub filters: Vec<BoundFilter>,
    pub group_by: Option<String>,
}

impl BoundQuery {
    /// Canonical string (`SPEC.md` §4.6), matching `QuerySpec.canonical()` in the oracle.
    pub fn canonical(&self) -> String {
        let mut filters_rendered: Vec<(String, String, String, String)> = self
            .filters
            .iter()
            .map(|f| {
                let (sort_val, repr_val) = match &f.value {
                    Value::I32(v) => (v.to_string(), v.to_string()),
                    Value::I64(v) | Value::Timestamp(v) => (v.to_string(), v.to_string()),
                    Value::F64(v) => {
                        let s = format!("{v:?}");
                        (s.clone(), s)
                    }
                    Value::Bool(true) => ("True".to_string(), "True".to_string()),
                    Value::Bool(false) => ("False".to_string(), "False".to_string()),
                    Value::Text(s) => (s.clone(), format!("'{}'", s.replace('\'', "\\'"))),
                    Value::Null => ("None".to_string(), "None".to_string()),
                };
                (
                    f.column.clone(),
                    f.op.canonical().to_string(),
                    sort_val,
                    repr_val,
                )
            })
            .collect();

        filters_rendered.sort_by(|a, b| {
            (a.0.as_str(), a.1.as_str(), a.2.as_str()).cmp(&(
                b.0.as_str(),
                b.1.as_str(),
                b.2.as_str(),
            ))
        });

        let mut parts = vec![
            self.agg.as_str().to_string(),
            self.column.clone().unwrap_or_else(|| "*".to_string()),
            self.table.clone(),
        ];
        for (col, op, _, repr_val) in filters_rendered {
            parts.push(format!("{col}{op}{repr_val}"));
        }
        parts.push(format!("GROUP={}", self.group_by.as_deref().unwrap_or("")));
        parts.join("|")
    }
}

/// Parse and bind `sql` against the default SafeHealth `MockCatalog`.
pub fn parse(sql: &str) -> Result<BoundQuery> {
    let catalog = MockCatalog::safehealth();
    parse_and_bind(sql, &catalog)
}

/// Parse `sql` with `sqlparser-rs` and bind against `catalog` (`SPEC.md` §3 and §9).
pub fn parse_and_bind(sql: &str, catalog: &dyn CatalogLookup) -> Result<BoundQuery> {
    let dialect = PostgreSqlDialect {};
    let mut stmts = Parser::parse_sql(&dialect, sql)
        .map_err(|e| Error::parse(format!("SQL syntax error: {e}")))?;

    if stmts.len() != 1 {
        return Err(Error::parse("expected exactly one SQL statement"));
    }

    let query = match stmts.pop().unwrap() {
        Statement::Query(q) => q,
        other => {
            return Err(Error::parse(format!(
                "only SELECT is supported, got {other}"
            )))
        }
    };

    bind_query(&query, catalog)
}

fn bind_query(query: &Query, catalog: &dyn CatalogLookup) -> Result<BoundQuery> {
    if query.with.is_some() {
        return Err(Error::parse("WITH (CTE) is not supported"));
    }
    if query.order_by.is_some() {
        return Err(Error::parse("ORDER BY is not supported"));
    }
    if query.limit.is_some() || query.offset.is_some() || query.fetch.is_some() {
        return Err(Error::parse("LIMIT/OFFSET is not supported"));
    }

    let select = match query.body.as_ref() {
        SetExpr::Select(s) => s,
        _ => return Err(Error::parse("UNION and set operations are not supported")),
    };

    if select.distinct.is_some() {
        return Err(Error::parse("DISTINCT is not supported"));
    }
    if select.having.is_some() {
        return Err(Error::parse("HAVING is not supported"));
    }
    if select.from.len() != 1 || !select.from[0].joins.is_empty() {
        return Err(Error::parse(
            "exactly one FROM table without JOINs is required",
        ));
    }

    let table_name = match &select.from[0].relation {
        TableFactor::Table {
            name, args: None, ..
        } => object_ident(name)?,
        _ => return Err(Error::parse("unsupported FROM relation")),
    };

    let table_meta = catalog
        .table(&table_name)
        .ok_or_else(|| Error::catalog(format!("unknown table: {table_name}")))?;

    if !table_meta.is_fact_table {
        return Err(Error::policy(format!(
            "only the privacy-unit fact table may be queried, not '{}'",
            table_meta.name
        )));
    }

    // Bind GROUP BY first so projection can allow `<group_col>, <agg>(...)` or `<agg>(...)`.
    let group_by = bind_group_by(&select.group_by, table_meta)?;
    let (agg, agg_col, clamp_bounds) =
        bind_projection(&select.projection, table_meta, group_by.as_deref())?;

    if agg == Agg::Avg && group_by.is_some() {
        return Err(Error::parse(
            "AVG with GROUP BY is not supported in v1 (SPEC 3)",
        ));
    }

    let mut filters = Vec::new();
    if let Some(selection) = &select.selection {
        bind_where(selection, table_meta, &mut filters)?;
    }

    Ok(BoundQuery {
        agg,
        column: agg_col,
        clamp_bounds,
        table: table_meta.name.clone(),
        max_rows_per_unit: table_meta.max_rows_per_unit,
        max_groups_per_unit: table_meta.max_groups_per_unit,
        filters,
        group_by,
    })
}

fn bind_group_by(group_by: &GroupByExpr, table: &TableMeta) -> Result<Option<String>> {
    let exprs = match group_by {
        GroupByExpr::Expressions(exprs, modifiers) if modifiers.is_empty() => exprs,
        GroupByExpr::All(_) | GroupByExpr::Expressions(_, _) => {
            return Err(Error::parse("unsupported GROUP BY modifier"))
        }
    };
    if exprs.is_empty() {
        return Ok(None);
    }
    if exprs.len() > 1 {
        return Err(Error::parse("at most one GROUP BY column is supported"));
    }
    let col_name = expr_column_ident(&exprs[0])?;
    let col = table
        .columns
        .get(&col_name)
        .ok_or_else(|| Error::catalog(format!("unknown column in GROUP BY: {col_name}")))?;
    if col.access == AccessMode::Denied {
        return Err(Error::policy(format!("column '{col_name}' is denied")));
    }
    if !col.groupable {
        return Err(Error::policy(format!(
            "column '{col_name}' is not groupable"
        )));
    }
    Ok(Some(col.name.clone()))
}

type BoundProjection = (Agg, Option<String>, Option<(f64, f64)>);

fn bind_projection(
    projection: &[SelectItem],
    table: &TableMeta,
    group_by: Option<&str>,
) -> Result<BoundProjection> {
    let agg_expr = match projection {
        [item] => select_item_expr(item)?,
        [first, second] if group_by.is_some() => {
            let g_col = expr_column_ident(select_item_expr(first)?)?;
            if Some(g_col.as_str()) != group_by {
                return Err(Error::parse(
                    "projected column must match the GROUP BY column",
                ));
            }
            select_item_expr(second)?
        }
        _ => return Err(Error::parse(
            "SELECT must project a single aggregate (optionally preceded by the GROUP BY column)",
        )),
    };

    let func = match agg_expr {
        Expr::Function(f) => f,
        _ => return Err(Error::parse("SELECT must project an aggregate function")),
    };

    if func.over.is_some() || func.filter.is_some() || !func.within_group.is_empty() {
        return Err(Error::parse(
            "window/filter aggregate modifiers are not supported",
        ));
    }

    let fn_name = object_ident(&func.name)?.to_ascii_uppercase();
    let agg = match fn_name.as_str() {
        "COUNT" => Agg::Count,
        "SUM" => Agg::Sum,
        "AVG" => Agg::Avg,
        other => return Err(Error::parse(format!("unsupported aggregate: {other}"))),
    };

    let args = match &func.args {
        FunctionArguments::List(list) => {
            if list.duplicate_treatment.is_some() || !list.clauses.is_empty() {
                return Err(Error::parse(
                    "DISTINCT or aggregate clauses are not supported",
                ));
            }
            &list.args
        }
        FunctionArguments::None => return Err(Error::parse("aggregate requires arguments")),
        FunctionArguments::Subquery(_) => return Err(Error::parse("subqueries are not supported")),
    };

    if args.len() != 1 {
        return Err(Error::parse("aggregate takes exactly one argument"));
    }

    match (&agg, &args[0]) {
        (Agg::Count, FunctionArg::Unnamed(FunctionArgExpr::Wildcard)) => {
            Ok((Agg::Count, None, None))
        }
        (Agg::Count, _) => Err(Error::parse(
            "only COUNT(*) is supported; COUNT(col) is rejected",
        )),
        (Agg::Sum | Agg::Avg, FunctionArg::Unnamed(FunctionArgExpr::Wildcard)) => Err(
            Error::parse(format!("{} requires a column name, not *", agg.as_str())),
        ),
        (Agg::Sum | Agg::Avg, FunctionArg::Unnamed(FunctionArgExpr::Expr(e))) => {
            let col_name = expr_column_ident(e)?;
            let col = table
                .columns
                .get(&col_name)
                .ok_or_else(|| Error::catalog(format!("unknown column: {col_name}")))?;
            if col.access == AccessMode::Denied {
                return Err(Error::policy(format!("column '{col_name}' is denied")));
            }
            if !col.col_type.is_numeric() {
                return Err(Error::policy(format!(
                    "{} requires a numeric column, got {} ({})",
                    agg.as_str(),
                    col.name,
                    col.col_type
                )));
            }
            let bounds = col.clamp_bounds.ok_or_else(|| {
                Error::policy(format!(
                    "{}({}) requires clamp bounds in the catalog policy",
                    agg.as_str(),
                    col.name
                ))
            })?;
            Ok((agg, Some(col.name.clone()), Some(bounds)))
        }
        _ => Err(Error::parse("unsupported aggregate argument")),
    }
}

fn bind_where(expr: &Expr, table: &TableMeta, out: &mut Vec<BoundFilter>) -> Result<()> {
    match expr {
        Expr::BinaryOp {
            left,
            op: BinaryOperator::And,
            right,
        } => {
            bind_where(left, table, out)?;
            bind_where(right, table, out)?;
            Ok(())
        }
        Expr::BinaryOp {
            op: BinaryOperator::Or,
            ..
        } => Err(Error::parse("OR in WHERE is not supported in v1")),
        Expr::UnaryOp {
            op: UnaryOperator::Not,
            ..
        } => Err(Error::parse("NOT in WHERE is not supported in v1")),
        Expr::BinaryOp { left, op, right } => {
            let cmp_op = match op {
                BinaryOperator::Eq => CmpOp::Eq,
                BinaryOperator::NotEq => CmpOp::Ne,
                BinaryOperator::Gt => CmpOp::Gt,
                BinaryOperator::GtEq => CmpOp::Gte,
                BinaryOperator::Lt => CmpOp::Lt,
                BinaryOperator::LtEq => CmpOp::Lte,
                other => return Err(Error::parse(format!("unsupported WHERE operator: {other}"))),
            };
            let col_name = expr_column_ident(left)?;
            let col = table
                .columns
                .get(&col_name)
                .ok_or_else(|| Error::catalog(format!("unknown column in WHERE: {col_name}")))?;
            if col.access == AccessMode::Denied {
                return Err(Error::policy(format!("column '{col_name}' is denied")));
            }
            if !col.filterable {
                return Err(Error::policy(format!(
                    "column '{col_name}' is not filterable"
                )));
            }
            let value = bind_literal(right, col.col_type)?;
            out.push(BoundFilter {
                column: col.name.clone(),
                op: cmp_op,
                value,
            });
            Ok(())
        }
        _ => Err(Error::parse(
            "WHERE clause must be a conjunction of <col> <op> <literal> predicates",
        )),
    }
}

fn bind_literal(expr: &Expr, expected: ColumnType) -> Result<Value> {
    let (neg, val) = match expr {
        Expr::UnaryOp {
            op: UnaryOperator::Minus,
            expr: inner,
        } => {
            if let Expr::Value(v) = inner.as_ref() {
                (true, v)
            } else {
                return Err(Error::parse(
                    "expected literal on right-hand side of comparison",
                ));
            }
        }
        Expr::Value(v) => (false, v),
        _ => {
            return Err(Error::parse(
                "expected literal on right-hand side of comparison",
            ))
        }
    };

    match (val, expected) {
        (SqlValue::Number(raw, _), ColumnType::I32) => {
            let signed = if neg { format!("-{raw}") } else { raw.clone() };
            let parsed = signed
                .parse::<i32>()
                .map_err(|_| Error::parse(format!("invalid i32 literal: {signed}")))?;
            Ok(Value::I32(parsed))
        }
        (SqlValue::Number(raw, _), ColumnType::I64) => {
            let signed = if neg { format!("-{raw}") } else { raw.clone() };
            let parsed = signed
                .parse::<i64>()
                .map_err(|_| Error::parse(format!("invalid i64 literal: {signed}")))?;
            Ok(Value::I64(parsed))
        }
        (SqlValue::Number(raw, _), ColumnType::F64) => {
            let signed = if neg { format!("-{raw}") } else { raw.clone() };
            let parsed = signed
                .parse::<f64>()
                .map_err(|_| Error::parse(format!("invalid f64 literal: {signed}")))?;
            Ok(Value::F64(parsed))
        }
        (SqlValue::SingleQuotedString(s), ColumnType::Text) if !neg => Ok(Value::Text(s.clone())),
        (SqlValue::SingleQuotedString(s), ColumnType::Timestamp) if !neg => {
            Ok(Value::Text(s.clone()))
        }
        (SqlValue::Number(raw, _), ColumnType::Timestamp) => {
            let signed = if neg { format!("-{raw}") } else { raw.clone() };
            let parsed = signed
                .parse::<i64>()
                .map_err(|_| Error::parse(format!("invalid timestamp literal: {signed}")))?;
            Ok(Value::Timestamp(parsed))
        }
        (SqlValue::Boolean(b), ColumnType::Bool) if !neg => Ok(Value::Bool(*b)),
        _ => Err(Error::parse(format!(
            "literal does not match column type {expected}"
        ))),
    }
}

fn select_item_expr(item: &SelectItem) -> Result<&Expr> {
    match item {
        SelectItem::UnnamedExpr(e) | SelectItem::ExprWithAlias { expr: e, .. } => Ok(e),
        _ => Err(Error::parse("wildcard SELECT * is not supported")),
    }
}

fn object_ident(name: &ObjectName) -> Result<String> {
    if name.0.len() != 1 {
        return Err(Error::parse("qualified identifiers are not supported"));
    }
    ident_str(&name.0[0])
}

fn expr_column_ident(expr: &Expr) -> Result<String> {
    match expr {
        Expr::Identifier(id) => ident_str(id),
        Expr::CompoundIdentifier(parts) if parts.len() == 2 => ident_str(&parts[1]),
        _ => Err(Error::parse("expected a column name")),
    }
}

fn ident_str(id: &Ident) -> Result<String> {
    Ok(id.value.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;
    use privguard_core::SqlState;

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
        assert_eq!(CmpOp::Eq.canonical(), "exact");
        assert_eq!(CmpOp::Ne.canonical(), "ne");
        assert_eq!(CmpOp::Gte.canonical(), "gte");
    }

    #[test]
    fn parses_and_binds_valid_spec_subset_queries() {
        let q1 = parse("SELECT COUNT(*) FROM visits WHERE dept_id = 2 AND total_charge > 500.0")
            .unwrap();
        assert_eq!(q1.agg, Agg::Count);
        assert_eq!(q1.column, None);
        assert_eq!(q1.filters.len(), 2);
        assert_eq!(
            q1.canonical(),
            "COUNT|*|visits|dept_idexact2|total_chargegt500.0|GROUP="
        );

        let q2 = parse(
            "SELECT admission_type, SUM(total_charge) FROM visits WHERE admission_type != 'TRAUMA' GROUP BY admission_type",
        )
        .unwrap();
        assert_eq!(q2.agg, Agg::Sum);
        assert_eq!(q2.column.as_deref(), Some("total_charge"));
        assert_eq!(q2.clamp_bounds, Some((0.0, 1000.0)));
        assert_eq!(q2.group_by.as_deref(), Some("admission_type"));
        assert_eq!(
            q2.canonical(),
            "SUM|total_charge|visits|admission_typene'TRAUMA'|GROUP=admission_type"
        );

        let q3 = parse("SELECT AVG(total_charge) FROM visits WHERE admission_type = 'EMERGENCY'")
            .unwrap();
        assert_eq!(q3.agg, Agg::Avg);
        assert_eq!(
            q3.canonical(),
            "AVG|total_charge|visits|admission_typeexact'EMERGENCY'|GROUP="
        );
    }

    #[test]
    fn unsupported_grammar_returns_spec9_parse_error_42601() {
        for bad_sql in [
            "SELECT * FROM visits",
            "SELECT COUNT(total_charge) FROM visits",
            "SELECT SUM(*) FROM visits",
            "SELECT MAX(total_charge) FROM visits",
            "SELECT AVG(total_charge) FROM visits GROUP BY dept_id",
            "SELECT COUNT(*) FROM visits WHERE dept_id = 1 OR dept_id = 2",
            "SELECT COUNT(*) FROM visits WHERE NOT dept_id = 1",
            "SELECT COUNT(*) FROM visits GROUP BY dept_id, admission_type",
            "SELECT COUNT(*) FROM visits LIMIT 10",
            "DELETE FROM visits",
        ] {
            let err = parse(bad_sql).unwrap_err();
            assert_eq!(
                err.sqlstate(),
                SqlState::SyntaxError,
                "expected 42601 ParseError for {bad_sql}, got {err:?}"
            );
        }
    }

    #[test]
    fn catalog_and_policy_violations_return_spec9_sqlstates() {
        // Unknown table or column -> 42P01 (UndefinedTable)
        assert_eq!(
            parse("SELECT COUNT(*) FROM nonexistent_table")
                .unwrap_err()
                .sqlstate(),
            SqlState::UndefinedTable
        );
        assert_eq!(
            parse("SELECT SUM(no_such_col) FROM visits")
                .unwrap_err()
                .sqlstate(),
            SqlState::UndefinedTable
        );

        // Non-fact table, denied column, non-numeric SUM, ungroupable/unfilterable column -> 42501
        for policy_sql in [
            "SELECT COUNT(*) FROM patients",
            "SELECT COUNT(*) FROM visits WHERE patient_id = 1",
            "SELECT COUNT(*) FROM visits WHERE visit_id = 1",
            "SELECT SUM(admission_type) FROM visits",
            "SELECT SUM(dept_id) FROM visits",
            "SELECT COUNT(*) FROM visits GROUP BY total_charge",
        ] {
            let err = parse(policy_sql).unwrap_err();
            assert_eq!(
                err.sqlstate(),
                SqlState::InsufficientPrivilege,
                "expected 42501 PolicyError for {policy_sql}, got {err:?}"
            );
        }
    }
}
