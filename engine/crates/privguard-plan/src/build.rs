//! Physical plan construction: from a bound query and the catalog to a tree of operators.
//!
//! Two shapes. [`build_exact`] is the non-private baseline (`Scan -> Filter -> Aggregate`),
//! used by the correctness tests and the benchmarks. [`build_dp`] is the private pipeline of
//! `SPEC.md` §4 / A1, with `plan_is_well_formed` checked before anything runs, so a plan that
//! reaches data without noise is a construction error rather than a runtime surprise.

use crate::exec::{Aggregate, Filter, GroupBy, HeapScan, IndexScan, Predicate};
use crate::{plan_is_well_formed, PhysicalOp};
use privguard_catalog::{Catalog, TableDef};
use privguard_core::Value;
use privguard_core::{ColumnType, Error, Result, RowSource};
use privguard_privacy::operators::{
    split_budget, AggKind, Calibration, Clamp, ContributionBound, DpAggConfig, DpAggregate,
    PartitionConfig, PartitionSelect,
};
use privguard_privacy::policy::{ColumnPolicy, Mechanism, TablePolicy};
use privguard_privacy::{defaults, PrivacyPolicy};
use privguard_sql::{Agg, BoundQuery, CmpOp, ColumnMeta, MockCatalog, TableMeta};
use std::collections::HashMap;
use std::sync::atomic::AtomicU64;
use std::sync::Arc;

/// The column every contribution bound counts distinct units over (`SPEC.md` §2.1).
pub const PRIVACY_UNIT: &str = "patient_id";

/// Resolve names for the binder from the persisted catalog.
pub fn catalog_lookup(catalog: &Catalog) -> MockCatalog {
    let mut out = MockCatalog::new();
    for t in catalog.tables() {
        let policy = t.privacy.unwrap_or(privguard_catalog::TablePrivacy {
            max_rows_per_unit: defaults::MAX_ROWS_PER_UNIT,
            max_groups_per_unit: defaults::MAX_GROUPS_PER_UNIT,
        });
        let columns: HashMap<String, ColumnMeta> = t
            .columns
            .iter()
            .map(|c| {
                (
                    c.name.clone(),
                    ColumnMeta {
                        name: c.name.clone(),
                        col_type: c.col_type,
                        access: t.access(&c.name),
                        filterable: c.filterable,
                        groupable: c.groupable,
                        clamp_bounds: t.column_privacy.get(&c.name).and_then(|p| p.clamp),
                    },
                )
            })
            .collect();
        out.insert_table(TableMeta {
            name: t.name.clone(),
            is_fact_table: t.is_fact,
            max_rows_per_unit: policy.max_rows_per_unit,
            max_groups_per_unit: policy.max_groups_per_unit,
            columns,
        });
    }
    out
}

fn table_of<'a>(catalog: &'a Catalog, name: &str) -> Result<&'a TableDef> {
    catalog
        .table(name)
        .ok_or_else(|| Error::catalog(format!("unknown table: {name}")))
}

/// `[lo, hi]` bounds on an `i64` column implied by the conjunction `preds`.
fn key_bounds(preds: &[Predicate], column: usize) -> (Option<i64>, Option<i64>) {
    let (mut lo, mut hi): (Option<i64>, Option<i64>) = (None, None);
    for p in preds.iter().filter(|p| p.column == column) {
        let v = match p.literal {
            Value::I64(v) => v,
            Value::I32(v) => i64::from(v),
            _ => continue,
        };
        let (l, h) = match p.op {
            CmpOp::Eq => (Some(v), Some(v)),
            CmpOp::Gt => (v.checked_add(1), None),
            CmpOp::Gte => (Some(v), None),
            CmpOp::Lt => (None, v.checked_sub(1)),
            CmpOp::Lte => (None, Some(v)),
            CmpOp::Ne => continue,
        };
        lo = match (lo, l) {
            (Some(a), Some(b)) => Some(a.max(b)),
            (a, b) => a.or(b),
        };
        hi = match (hi, h) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        };
    }
    (lo, hi)
}

/// Rows of `table` that can satisfy `preds`: an index range when an indexed `i64` column is
/// constrained, a sequential scan otherwise. The caller still applies every predicate, so the
/// choice affects speed only.
pub fn scan(
    catalog: &Catalog,
    table: &TableDef,
    preds: &[Predicate],
) -> Result<Box<dyn RowSource>> {
    let heap = catalog.open_heap(&table.name)?;
    let types = table.column_types();
    for (i, col) in table.columns.iter().enumerate() {
        if col.col_type != ColumnType::I64 || !catalog.index_path(&table.name, &col.name).exists() {
            continue;
        }
        let (lo, hi) = key_bounds(preds, i);
        if lo.is_some() || hi.is_some() {
            let index = catalog.open_index(&table.name, &col.name)?;
            return Ok(Box::new(IndexScan::new(&heap, &index, lo, hi, types)?));
        }
    }
    Ok(Box::new(HeapScan::new(&heap, types)?))
}

fn compile_filters(table: &TableDef, query: &BoundQuery) -> Result<Vec<Predicate>> {
    query
        .filters
        .iter()
        .map(|f| Predicate::compile(table, f))
        .collect()
}

fn column_index(table: &TableDef, name: &str) -> Result<usize> {
    table
        .column(name)
        .map(|(i, _)| i)
        .ok_or_else(|| Error::catalog(format!("unknown column: {}.{name}", table.name)))
}

/// Non-private baseline. Output rows are `[value]` or `[group, value]`.
pub fn build_exact(catalog: &Catalog, query: &BoundQuery) -> Result<Box<dyn RowSource>> {
    let table = table_of(catalog, &query.table)?;
    let preds = compile_filters(table, query)?;
    let mut src: Box<dyn RowSource> = scan(catalog, table, &preds)?;
    if !preds.is_empty() {
        src = Box::new(Filter::new(src, preds));
    }
    let value = query
        .column
        .as_deref()
        .map(|c| column_index(table, c))
        .transpose()?;
    Ok(match &query.group_by {
        Some(g) => Box::new(GroupBy::new(src, query.agg, value, column_index(table, g)?)),
        None => Box::new(Aggregate::new(src, query.agg, value)),
    })
}

/// The private plan, ready to run, with what the caller needs to report it.
pub struct DpPlan {
    pub source: Box<dyn RowSource>,
    pub calibration: Calibration,
    /// Groups removed by partition selection; final once `source` is drained.
    pub suppressed: Option<Arc<AtomicU64>>,
    pub operators: Vec<PhysicalOp>,
}

fn agg_kind(agg: Agg) -> AggKind {
    match agg {
        Agg::Count => AggKind::Count,
        Agg::Sum => AggKind::Sum,
        Agg::Avg => AggKind::Avg,
    }
}

/// The table and column policy a query is calibrated against. The bounds come from the bound
/// query (the binder resolved them from the catalog); only the mechanism name is looked up here.
pub fn privacy_policy(catalog: &Catalog, query: &BoundQuery) -> Result<PrivacyPolicy> {
    let table = table_of(catalog, &query.table)?;
    let column = match (&query.column, query.clamp_bounds) {
        (Some(col), Some((lo, hi))) => {
            let mech = table
                .column_privacy
                .get(col)
                .and_then(|p| p.mechanism.as_deref())
                .map(Mechanism::parse)
                .transpose()?
                .unwrap_or(Mechanism::Laplace);
            Some(ColumnPolicy::new(mech, lo, hi)?)
        }
        (Some(col), None) => {
            return Err(Error::policy(format!(
                "{col} has no clamp bounds in its policy"
            )))
        }
        _ => None,
    };
    Ok(PrivacyPolicy::new(
        TablePolicy::new(query.max_rows_per_unit, query.max_groups_per_unit)?,
        column,
    ))
}

/// `Scan -> Filter -> ContributionBound -> Clamp -> [PartitionSelect] -> DPAggregate`.
///
/// `seed` is the query's noise seed (`SPEC.md` §4.4); the caller derives it so that the same
/// fingerprint and principal always get the same noise.
pub fn build_dp(catalog: &Catalog, query: &BoundQuery, rho: f64, seed: &[u8]) -> Result<DpPlan> {
    let table = table_of(catalog, &query.table)?;
    let policy = privacy_policy(catalog, query)?;
    let preds = compile_filters(table, query)?;
    let unit = column_index(table, PRIVACY_UNIT)?;
    let group = query
        .group_by
        .as_deref()
        .map(|g| column_index(table, g))
        .transpose()?;
    let value = query
        .column
        .as_deref()
        .map(|c| column_index(table, c))
        .transpose()?;

    let mut ops = vec![PhysicalOp::Scan];
    let mut src: Box<dyn RowSource> = scan(catalog, table, &preds)?;
    if !preds.is_empty() {
        src = Box::new(Filter::new(src, preds));
        ops.push(PhysicalOp::Filter);
    }

    // The first column is the primary key, which gives the bound its deterministic row order.
    src = Box::new(ContributionBound::new(src, unit, 0, group, policy.table));
    ops.push(PhysicalOp::ContributionBound);

    if let (Some(v), Some(cp)) = (value, &policy.column) {
        src = Box::new(Clamp::new(src, v, cp));
        ops.push(PhysicalOp::Clamp);
    }

    let mut suppressed = None;
    if let Some(g) = group {
        let ps = PartitionSelect::new(
            src,
            PartitionConfig {
                unit,
                group: g,
                rho_part: split_budget(rho).0,
                delta_part: defaults::DELTA_PARTITION,
                seed: seed.to_vec(),
            },
        )?;
        suppressed = Some(ps.suppressed_counter());
        src = Box::new(ps);
        ops.push(PhysicalOp::PartitionSelect);
    }

    let cfg = DpAggConfig {
        agg: agg_kind(query.agg),
        value,
        group,
        table: policy.table,
        column: policy.column,
        rho,
        delta: catalog.default_delta,
        seed: seed.to_vec(),
    };
    let calibration = cfg.calibration()?;
    let source: Box<dyn RowSource> = Box::new(DpAggregate::new(src, cfg)?);
    ops.push(PhysicalOp::DPAggregate);

    if !plan_is_well_formed(&ops) {
        return Err(Error::engine(format!("built an ill-formed plan: {ops:?}")));
    }
    Ok(DpPlan {
        source,
        calibration,
        suppressed,
        operators: ops,
    })
}

/// Sensitivity, scale and mechanism for `query` at `rho`, computed from policy alone.
///
/// Needed before the budget reservation (the ledger records how the charge was calibrated), and
/// the reservation must precede any read of data, so this touches no storage.
pub fn calibrate(catalog: &Catalog, query: &BoundQuery, rho: f64) -> Result<Calibration> {
    let policy = privacy_policy(catalog, query)?;
    DpAggConfig {
        agg: agg_kind(query.agg),
        value: query.column.as_ref().map(|_| 0),
        group: query.group_by.as_ref().map(|_| 0),
        table: policy.table,
        column: policy.column,
        rho,
        delta: catalog.default_delta,
        seed: Vec::new(),
    }
    .calibration()
}
