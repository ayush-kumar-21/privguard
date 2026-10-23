//! Physical DP operators over [`RowSource`]. `SPEC.md` §4 (`IMPLEMENTATION_PLAN.md` 3.3).
//!
//! The pipeline mirrors `oracle/privguard_oracle/dp.py`:
//!
//! ```text
//! rows -> ContributionBound -> Clamp -> [PartitionSelect] -> DPAggregate
//! ```
//!
//! Each operator is a `RowSource` wrapping another, so they run over the real executor and over
//! in-memory rows alike. Column positions are passed in, which keeps the operators independent
//! of any table schema. `PartitionSelect` sits before `DPAggregate` (see `docs/phase2-design.md`
//! §6 and `SPEC.md` §10 A1): the oracle draws aggregate noise only for surviving groups, so the
//! survivors have to be known first.
//!
//! Every operator that must see all of its input before emitting (all of them except `Clamp`)
//! drains it on the first `next_row` call.

use crate::accounting::{
    eps_from_rho, grouped_l2_sensitivity, laplace_scale_from_rho, sigma_from_rho,
};
use crate::defaults::PARTITION_BUDGET_FRACTION;
use crate::mechanisms::{
    clamp, noisy_count, noisy_gaussian, noisy_laplace, partition_cutoff, partition_release,
    KeyedRandom,
};
use crate::policy::{ColumnPolicy, TablePolicy};
use privguard_core::{Error, Result, Row, RowSource, Value};
use std::cmp::Ordering;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};
use std::sync::Arc;

/// Total order over values, used for grouping and for sorting groups. Mirrors Python's
/// ordering on the same-typed values the oracle sorts; `Null` sorts first, and values of
/// different types are ordered by type so the order is total rather than a panic.
fn value_cmp(a: &Value, b: &Value) -> Ordering {
    fn tag(v: &Value) -> u8 {
        match v {
            Value::Null => 0,
            Value::Bool(_) => 1,
            Value::I32(_) | Value::I64(_) | Value::Timestamp(_) => 2,
            Value::F64(_) => 3,
            Value::Text(_) => 4,
        }
    }
    let int = |v: &Value| match v {
        Value::I32(x) => i64::from(*x),
        Value::I64(x) | Value::Timestamp(x) => *x,
        _ => 0,
    };
    match (a, b) {
        (Value::Bool(x), Value::Bool(y)) => x.cmp(y),
        (Value::F64(x), Value::F64(y)) => x.total_cmp(y),
        (Value::Text(x), Value::Text(y)) => x.as_bytes().cmp(y.as_bytes()),
        _ if tag(a) == 2 && tag(b) == 2 => int(a).cmp(&int(b)),
        _ => tag(a).cmp(&tag(b)),
    }
}

/// A [`Value`] with the total order of [`value_cmp`], usable as a map key.
#[derive(Debug, Clone)]
struct Key(Value);

impl PartialEq for Key {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for Key {}
impl PartialOrd for Key {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Key {
    fn cmp(&self, other: &Self) -> Ordering {
        value_cmp(&self.0, &other.0)
    }
}

fn cell<'a>(row: &'a Row, idx: usize, what: &str) -> Result<&'a Value> {
    row.get(idx)
        .ok_or_else(|| Error::engine(format!("row has no {what} column at position {idx}")))
}

/// `round(x, 4)` as Python computes it: correctly rounded decimal, then back to a float.
fn round4(x: f64) -> f64 {
    format!("{x:.4}").parse().unwrap_or(x)
}

/// Per-row numeric value for aggregation: `NULL` contributes zero, as in the oracle.
fn numeric(v: &Value) -> Result<f64> {
    match v {
        Value::Null => Ok(0.0),
        other => other
            .as_f64()
            .ok_or_else(|| Error::engine(format!("cannot aggregate non-numeric value {other:?}"))),
    }
}

/// Run `compute` once on the first call, then hand out its rows one by one.
#[derive(Debug, Default)]
struct Staged {
    rows: Option<std::vec::IntoIter<Row>>,
}

// ------------------------------------------------------------- ContributionBound

/// L∞ and L0 contribution bounding. `SPEC.md` §4.2.
///
/// Ungrouped: each privacy unit keeps its first `C` rows by `order` column. Grouped: the unit
/// keeps its `G` smallest group values, then the first `C` rows within each. Selection is
/// deterministic because the noise cache promises the same answer for the same fingerprint.
/// Output is sorted by the order column.
pub struct ContributionBound<S: RowSource> {
    input: S,
    unit: usize,
    order: usize,
    group: Option<usize>,
    policy: TablePolicy,
    out: Staged,
}

impl<S: RowSource> ContributionBound<S> {
    pub fn new(
        input: S,
        unit: usize,
        order: usize,
        group: Option<usize>,
        policy: TablePolicy,
    ) -> Self {
        ContributionBound {
            input,
            unit,
            order,
            group,
            policy,
            out: Staged::default(),
        }
    }

    fn bound(&mut self) -> Result<Vec<Row>> {
        let (unit, order, group) = (self.unit, self.order, self.group);
        let c = self.policy.max_rows_per_unit as usize;
        let g = self.policy.max_groups_per_unit as usize;

        let mut by_unit: BTreeMap<Key, Vec<Row>> = BTreeMap::new();
        while let Some(row) = self.input.next_row()? {
            let u = Key(cell(&row, unit, "privacy-unit")?.clone());
            cell(&row, order, "order-key")?;
            if let Some(gi) = group {
                cell(&row, gi, "group")?;
            }
            by_unit.entry(u).or_default().push(row);
        }

        let by_order = |a: &Row, b: &Row| value_cmp(&a[order], &b[order]);
        let mut kept: Vec<Row> = Vec::new();
        for (_, mut rows) in by_unit {
            let Some(gi) = group else {
                rows.sort_by(by_order);
                kept.extend(rows.into_iter().take(c));
                continue;
            };
            let mut per_group: BTreeMap<Key, Vec<Row>> = BTreeMap::new();
            for r in rows {
                per_group.entry(Key(r[gi].clone())).or_default().push(r);
            }
            // BTreeMap iterates groups ascending: the first G are the unit's lowest groups.
            for (_, mut grp) in per_group.into_iter().take(g) {
                grp.sort_by(by_order);
                kept.extend(grp.into_iter().take(c));
            }
        }
        kept.sort_by(by_order);
        Ok(kept)
    }
}

impl<S: RowSource> RowSource for ContributionBound<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        if self.out.rows.is_none() {
            let bounded = self.bound()?;
            self.out.rows = Some(bounded.into_iter());
        }
        Ok(self.out.rows.as_mut().and_then(Iterator::next))
    }
}

// ------------------------------------------------------------------------ Clamp

/// Clamp one column into `[lo, hi]`, which is what gives `SUM` a finite sensitivity.
/// `NULL` counts as 0 first (as in the oracle); the column becomes `F64`.
pub struct Clamp<S: RowSource> {
    input: S,
    column: usize,
    lo: f64,
    hi: f64,
}

impl<S: RowSource> Clamp<S> {
    pub fn new(input: S, column: usize, policy: &ColumnPolicy) -> Self {
        Clamp {
            input,
            column,
            lo: policy.lower_clamp,
            hi: policy.upper_clamp,
        }
    }
}

impl<S: RowSource> RowSource for Clamp<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        let Some(mut row) = self.input.next_row()? else {
            return Ok(None);
        };
        let v = numeric(cell(&row, self.column, "clamped")?)?;
        row[self.column] = Value::F64(clamp(v, Some(self.lo), Some(self.hi)));
        Ok(Some(row))
    }
}

// ------------------------------------------------------------- PartitionSelect

/// Split a query's ρ between partition selection and the aggregate. `SPEC.md` §4.5.
pub fn split_budget(rho: f64) -> (f64, f64) {
    let part = rho * PARTITION_BUDGET_FRACTION;
    (part, rho - part)
}

/// Configuration of [`PartitionSelect`].
#[derive(Debug, Clone)]
pub struct PartitionConfig {
    pub unit: usize,
    pub group: usize,
    /// ρ spent on partition selection (`split_budget(rho).0`).
    pub rho_part: f64,
    pub delta_part: f64,
    /// The query's noise seed; the partition stream is `seed ‖ "partition"`.
    pub seed: Vec<u8>,
}

/// DP partition selection. `SPEC.md` §4.5.
///
/// A group survives iff `distinct_units` plus exact discrete-Laplace noise clears the cutoff
/// (`partition_cutoff`). Rows of suppressed groups are
/// dropped; their count is reported through the counter returned by
/// [`suppressed_counter`](Self::suppressed_counter) (identities are never released).
pub struct PartitionSelect<S: RowSource> {
    input: S,
    cfg: PartitionConfig,
    suppressed: Arc<AtomicU64>,
    out: Staged,
}

impl<S: RowSource> PartitionSelect<S> {
    pub fn new(input: S, cfg: PartitionConfig) -> Result<Self> {
        if cfg.rho_part <= 0.0 {
            return Err(Error::policy("partition selection needs a positive budget"));
        }
        Ok(PartitionSelect {
            input,
            cfg,
            suppressed: Arc::new(AtomicU64::new(0)),
            out: Staged::default(),
        })
    }

    /// Shared counter of suppressed groups; final once the operator is exhausted.
    pub fn suppressed_counter(&self) -> Arc<AtomicU64> {
        Arc::clone(&self.suppressed)
    }

    fn select(&mut self) -> Result<Vec<Row>> {
        let cfg = &self.cfg;
        let mut groups: BTreeMap<Key, (BTreeMap<Key, ()>, Vec<Row>)> = BTreeMap::new();
        while let Some(row) = self.input.next_row()? {
            let u = Key(cell(&row, cfg.unit, "privacy-unit")?.clone());
            let g = Key(cell(&row, cfg.group, "group")?.clone());
            let entry = groups.entry(g).or_default();
            entry.0.insert(u, ());
            entry.1.push(row);
        }

        let cut = partition_cutoff(cfg.rho_part, cfg.delta_part)?;
        let mut seed = cfg.seed.clone();
        seed.extend_from_slice(b"partition");
        let mut rng = KeyedRandom::new(&seed);

        let mut released = Vec::new();
        for (_, (units, rows)) in groups {
            if partition_release(units.len() as u64, &cut, &mut rng) {
                released.extend(rows);
            } else {
                self.suppressed.fetch_add(1, AtomicOrdering::Relaxed);
            }
        }
        Ok(released)
    }
}

impl<S: RowSource> RowSource for PartitionSelect<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        if self.out.rows.is_none() {
            let rows = self.select()?;
            self.out.rows = Some(rows.into_iter());
        }
        Ok(self.out.rows.as_mut().and_then(Iterator::next))
    }
}

// ------------------------------------------------------------------- DPAggregate

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AggKind {
    Count,
    Sum,
    Avg,
}

/// Configuration of [`DpAggregate`].
#[derive(Debug, Clone)]
pub struct DpAggConfig {
    pub agg: AggKind,
    /// Position of the (already clamped) aggregated column; `None` for `COUNT(*)`.
    pub value: Option<usize>,
    pub group: Option<usize>,
    pub table: TablePolicy,
    pub column: Option<ColumnPolicy>,
    /// The query's whole ρ.
    pub rho: f64,
    pub delta: f64,
    /// The query's noise seed (`SPEC.md` §4.4).
    pub seed: Vec<u8>,
}

/// How a query's noise was calibrated; reported alongside the answer.
#[derive(Debug, Clone, PartialEq)]
pub struct Calibration {
    pub mechanism: &'static str,
    pub sensitivity: f64,
    pub scale: f64,
    pub rho_charged: f64,
    pub eps_equivalent: f64,
}

impl DpAggConfig {
    fn validate(&self) -> Result<()> {
        if self.rho <= 0.0 {
            return Err(Error::policy("rho must be positive"));
        }
        match self.agg {
            AggKind::Count => {
                if self.value.is_some() {
                    return Err(Error::policy("only COUNT(*) is supported"));
                }
            }
            AggKind::Sum | AggKind::Avg => {
                if self.value.is_none() || self.column.is_none() {
                    return Err(Error::policy(
                        "SUM and AVG need a column with clamp bounds in its policy",
                    ));
                }
            }
        }
        if self.agg == AggKind::Avg && self.group.is_some() {
            return Err(Error::policy("AVG with GROUP BY is not supported"));
        }
        Ok(())
    }

    /// Sensitivity, scale and mechanism, known before any data is read.
    pub fn calibration(&self) -> Result<Calibration> {
        self.validate()?;
        let c = f64::from(self.table.max_rows_per_unit);
        let m = self.column.map_or(1.0, |p| p.magnitude());
        let (mechanism, sensitivity, scale) = match (self.group, self.agg) {
            (None, AggKind::Count) => ("discrete_laplace", c, laplace_scale_from_rho(c, self.rho)),
            (None, AggKind::Sum) => ("laplace", c * m, laplace_scale_from_rho(c * m, self.rho)),
            // AVG reports the sum half, as the oracle does.
            (None, AggKind::Avg) => (
                "laplace",
                c * m,
                laplace_scale_from_rho(c * m, self.rho / 2.0),
            ),
            (Some(_), agg) => {
                let per_unit = if agg == AggKind::Sum { m } else { 1.0 };
                let delta2 = grouped_l2_sensitivity(self.table.max_groups_per_unit, c * per_unit);
                let (_, rho_agg) = split_budget(self.rho);
                ("gaussian", delta2, sigma_from_rho(delta2, rho_agg))
            }
        };
        Ok(Calibration {
            mechanism,
            sensitivity,
            scale,
            rho_charged: self.rho,
            eps_equivalent: eps_from_rho(self.rho, self.delta),
        })
    }
}

/// The aggregate plus calibrated noise.
///
/// Scalar output is one row `[value]` (`I64` for `COUNT`, `F64` otherwise). Grouped output is
/// one row `[group, value]` per group in ascending group order, all `F64` values. Values are
/// rounded to 4 decimals, as in the oracle.
pub struct DpAggregate<S: RowSource> {
    input: S,
    cfg: DpAggConfig,
    out: Staged,
}

impl<S: RowSource> DpAggregate<S> {
    pub fn new(input: S, cfg: DpAggConfig) -> Result<Self> {
        cfg.validate()?;
        Ok(DpAggregate {
            input,
            cfg,
            out: Staged::default(),
        })
    }

    fn aggregate(&mut self) -> Result<Vec<Row>> {
        let cfg = &self.cfg;
        let value_of = |row: &Row| -> Result<f64> {
            match cfg.value {
                Some(i) => numeric(cell(row, i, "aggregated")?),
                None => Ok(0.0),
            }
        };

        cfg.calibration()?; // validates the configuration before any noise is drawn
        let c = f64::from(cfg.table.max_rows_per_unit);
        let m = cfg.column.map_or(1.0, |p| p.magnitude());
        let Some(gi) = cfg.group else {
            let (mut n, mut total) = (0u64, 0.0f64);
            while let Some(row) = self.input.next_row()? {
                n += 1;
                total += value_of(&row)?;
            }
            let mut rng = KeyedRandom::new(&cfg.seed);
            let value = match cfg.agg {
                AggKind::Count => {
                    return Ok(vec![vec![Value::I64(noisy_count(
                        n as i64, c, cfg.rho, &mut rng,
                    )?)]]);
                }
                AggKind::Sum => noisy_laplace(total, c * m, cfg.rho, &mut rng)?,
                AggKind::Avg => {
                    let noisy_sum = noisy_laplace(total, c * m, cfg.rho / 2.0, &mut rng)?;
                    let noisy_cnt = noisy_laplace(n as f64, c, cfg.rho / 2.0, &mut rng)?;
                    noisy_sum / noisy_cnt.max(1.0)
                }
            };
            return Ok(vec![vec![Value::F64(round4(value))]]);
        };

        let mut groups: BTreeMap<Key, (u64, f64)> = BTreeMap::new();
        while let Some(row) = self.input.next_row()? {
            let g = Key(cell(&row, gi, "group")?.clone());
            let e = groups.entry(g).or_insert((0, 0.0));
            e.0 += 1;
            e.1 += value_of(&row)?;
        }
        let mut seed = cfg.seed.clone();
        seed.extend_from_slice(b"aggregate");
        let mut rng = KeyedRandom::new(&seed);
        let per_unit = if cfg.agg == AggKind::Sum { m } else { 1.0 };
        let (_, rho_agg) = split_budget(cfg.rho);
        groups
            .into_iter()
            .map(|(g, (n, total))| {
                let truth = if cfg.agg == AggKind::Sum {
                    total
                } else {
                    n as f64
                };
                let noisy = noisy_gaussian(
                    truth,
                    c * per_unit,
                    cfg.table.max_groups_per_unit,
                    rho_agg,
                    &mut rng,
                )?;
                Ok(vec![g.0, Value::F64(round4(noisy))])
            })
            .collect()
    }
}

impl<S: RowSource> RowSource for DpAggregate<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        if self.out.rows.is_none() {
            let rows = self.aggregate()?;
            self.out.rows = Some(rows.into_iter());
        }
        Ok(self.out.rows.as_mut().and_then(Iterator::next))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::policy::Mechanism;
    use privguard_core::VecRowSource;

    /// Row layout used throughout: `[visit_id, patient_id, group, charge]`.
    fn r(visit: i64, patient: i64, group: &str, charge: Option<f64>) -> Row {
        vec![
            Value::I64(visit),
            Value::I64(patient),
            Value::Text(group.into()),
            charge.map_or(Value::Null, Value::F64),
        ]
    }

    fn policy(c: u32, g: u32) -> TablePolicy {
        TablePolicy::new(c, g).unwrap()
    }

    fn drain(mut s: impl RowSource) -> Vec<Row> {
        s.collect_rows().unwrap()
    }

    fn ids(rows: &[Row]) -> Vec<i64> {
        rows.iter()
            .map(|r| match r[0] {
                Value::I64(v) => v,
                _ => panic!("visit id"),
            })
            .collect()
    }

    #[test]
    fn round4_matches_python_round() {
        assert_eq!(round4(2.5), 2.5);
        assert_eq!(round4(0.12345678), 0.1235);
        assert_eq!(round4(-0.12344), -0.1234);
    }

    #[test]
    fn bound_keeps_each_units_first_c_rows_by_key() {
        let rows = vec![
            r(5, 1, "a", None),
            r(1, 1, "a", None),
            r(3, 1, "a", None),
            r(2, 2, "a", None),
            r(4, 1, "a", None),
        ];
        let out = drain(ContributionBound::new(
            VecRowSource::new(rows),
            1,
            0,
            None,
            policy(2, 3),
        ));
        // Patient 1 keeps visits 1 and 3; patient 2 keeps its only visit; sorted by key.
        assert_eq!(ids(&out), vec![1, 2, 3]);
    }

    #[test]
    fn grouped_bound_applies_l0_then_l_inf() {
        // Patient 1 touches groups a,b,c; with G = 2 only a and b survive, C = 1 per group.
        let rows = vec![
            r(1, 1, "c", None),
            r(2, 1, "b", None),
            r(3, 1, "a", None),
            r(4, 1, "a", None),
            r(5, 1, "b", None),
        ];
        let out = drain(ContributionBound::new(
            VecRowSource::new(rows),
            1,
            0,
            Some(2),
            policy(1, 2),
        ));
        assert_eq!(ids(&out), vec![2, 3]);
    }

    #[test]
    fn bound_is_independent_of_input_order() {
        let mut rows: Vec<Row> = (1..=12).map(|i| r(i, i % 3, "a", None)).collect();
        let fwd = drain(ContributionBound::new(
            VecRowSource::new(rows.clone()),
            1,
            0,
            None,
            policy(2, 1),
        ));
        rows.reverse();
        let rev = drain(ContributionBound::new(
            VecRowSource::new(rows),
            1,
            0,
            None,
            policy(2, 1),
        ));
        assert_eq!(fwd, rev);
    }

    #[test]
    fn clamp_zeroes_nulls_and_limits_both_sides() {
        let col = ColumnPolicy::new(Mechanism::Laplace, 0.0, 100.0).unwrap();
        let rows = vec![
            r(1, 1, "a", Some(-5.0)),
            r(2, 1, "a", Some(50.0)),
            r(3, 1, "a", Some(500.0)),
            r(4, 1, "a", None),
        ];
        let out = drain(Clamp::new(VecRowSource::new(rows), 3, &col));
        let vals: Vec<f64> = out.iter().map(|r| r[3].as_f64().unwrap()).collect();
        assert_eq!(vals, vec![0.0, 50.0, 100.0, 0.0]);
    }

    #[test]
    fn clamp_rejects_text() {
        let col = ColumnPolicy::new(Mechanism::Laplace, 0.0, 1.0).unwrap();
        let mut op = Clamp::new(VecRowSource::new(vec![r(1, 1, "a", None)]), 2, &col);
        assert!(op.next_row().is_err());
    }

    fn cfg(agg: AggKind, group: bool) -> DpAggConfig {
        DpAggConfig {
            agg,
            value: (agg != AggKind::Count).then_some(3),
            group: group.then_some(2),
            table: policy(5, 3),
            column: (agg != AggKind::Count)
                .then(|| ColumnPolicy::new(Mechanism::Laplace, 0.0, 1000.0).unwrap()),
            rho: 0.5,
            delta: 1e-6,
            seed: b"seed".to_vec(),
        }
    }

    #[test]
    fn calibration_follows_spec_4_3() {
        let cal = cfg(AggKind::Count, false).calibration().unwrap();
        assert_eq!((cal.mechanism, cal.sensitivity), ("discrete_laplace", 5.0));
        assert!((cal.scale - 5.0).abs() < 1e-12); // 5 / sqrt(2 * 0.5)
        let cal = cfg(AggKind::Sum, false).calibration().unwrap();
        assert_eq!((cal.mechanism, cal.sensitivity), ("laplace", 5000.0));
        let cal = cfg(AggKind::Count, true).calibration().unwrap();
        assert_eq!(cal.mechanism, "gaussian");
        assert!((cal.sensitivity - 3f64.sqrt() * 5.0).abs() < 1e-12);
    }

    #[test]
    fn invalid_aggregate_configs_are_policy_errors() {
        let mut bad = cfg(AggKind::Sum, false);
        bad.column = None;
        assert_eq!(bad.calibration().unwrap_err().sqlstate().code(), "42501");
        let mut bad = cfg(AggKind::Avg, true);
        assert!(bad.calibration().is_err());
        bad = cfg(AggKind::Count, false);
        bad.rho = 0.0;
        assert!(bad.calibration().is_err());
        bad = cfg(AggKind::Count, false);
        bad.value = Some(3);
        assert!(bad.calibration().is_err());
    }

    #[test]
    fn same_seed_gives_the_same_answer_and_a_new_seed_a_different_one() {
        let rows: Vec<Row> = (1..=40).map(|i| r(i, i, "a", Some(10.0))).collect();
        let run = |seed: &[u8]| {
            let mut c = cfg(AggKind::Sum, false);
            c.seed = seed.to_vec();
            drain(DpAggregate::new(VecRowSource::new(rows.clone()), c).unwrap())
        };
        assert_eq!(run(b"one"), run(b"one"));
        assert_ne!(run(b"one"), run(b"two"));
    }

    #[test]
    fn grouped_output_is_sorted_by_group_with_f64_values() {
        let rows = vec![
            r(1, 1, "b", Some(1.0)),
            r(2, 2, "a", Some(1.0)),
            r(3, 3, "b", Some(1.0)),
        ];
        let out =
            drain(DpAggregate::new(VecRowSource::new(rows), cfg(AggKind::Count, true)).unwrap());
        assert_eq!(out.len(), 2);
        assert_eq!(out[0][0], Value::Text("a".into()));
        assert_eq!(out[1][0], Value::Text("b".into()));
        assert!(matches!(out[0][1], Value::F64(_)));
    }

    #[test]
    fn partition_select_suppresses_small_groups_and_counts_them() {
        // Group "big" has 60 distinct patients, "tiny" has 1.
        let mut rows: Vec<Row> = (1..=60).map(|i| r(i, i, "big", None)).collect();
        rows.push(r(100, 100, "tiny", None));
        let (rho_part, _) = split_budget(2.0);
        let op = PartitionSelect::new(
            VecRowSource::new(rows),
            PartitionConfig {
                unit: 1,
                group: 2,
                rho_part,
                delta_part: 1e-2,
                seed: b"s".to_vec(),
            },
        )
        .unwrap();
        let counter = op.suppressed_counter();
        let out = drain(op);
        assert_eq!(out.len(), 60);
        assert!(out.iter().all(|r| r[2] == Value::Text("big".into())));
        assert_eq!(counter.load(AtomicOrdering::Relaxed), 1);
    }

    #[test]
    fn partition_select_needs_budget() {
        let err = PartitionSelect::new(
            VecRowSource::new(vec![]),
            PartitionConfig {
                unit: 0,
                group: 0,
                rho_part: 0.0,
                delta_part: 1e-2,
                seed: vec![],
            },
        );
        assert!(err.is_err());
    }

    #[test]
    fn value_order_is_total_and_python_like() {
        assert_eq!(value_cmp(&Value::I32(2), &Value::I64(10)), Ordering::Less);
        assert_eq!(
            value_cmp(&Value::Text("TRAUMA".into()), &Value::Text("URGENT".into())),
            Ordering::Less
        );
        assert_eq!(value_cmp(&Value::Null, &Value::I64(0)), Ordering::Less);
        assert_eq!(
            value_cmp(&Value::F64(f64::NAN), &Value::F64(f64::NAN)),
            Ordering::Equal
        );
    }
}
