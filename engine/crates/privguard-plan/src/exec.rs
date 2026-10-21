//! Volcano executor operators over heap files and B+ tree indexes.
//! `IMPLEMENTATION_PLAN.md` 2.8.
//!
//! Every operator is a [`RowSource`], so they stack the same way the DP operators do:
//! `HeapScan | IndexScan -> Filter -> Aggregate | GroupBy`. These are the *non-private*
//! operators; the DP pipeline reuses `HeapScan`, `IndexScan` and `Filter` and replaces the
//! aggregate with the operators in `privguard-privacy`.
//!
//! `NULL` follows the DP pipeline, not SQL: it counts as zero in `SUM`/`AVG` and `AVG` divides by
//! the row count. That keeps the baseline and the private answer differing only by noise and
//! contribution bounding, which is what the benchmarks and the correctness tests compare.

use privguard_catalog::loader::parse_timestamp;
use privguard_catalog::TableDef;
use privguard_core::{ColumnType, Error, Result, Rid, Row, RowSource, Value};
use privguard_sql::{Agg, BoundFilter, CmpOp};
use privguard_storage::{decode_row_with, BPlusTree, HeapFile, PageStore, ValueCipher};
use std::cmp::Ordering;

// -------------------------------------------------------------------- scans

/// Sequential scan of a heap file in `(page, slot)` order.
pub struct HeapScan {
    tuples: std::vec::IntoIter<Vec<u8>>,
    types: Vec<ColumnType>,
    cipher: Option<Box<dyn ValueCipher>>,
}

impl HeapScan {
    pub fn new<S: PageStore>(heap: &HeapFile<S>, types: Vec<ColumnType>) -> Result<Self> {
        Self::with_cipher(heap, types, None)
    }

    /// Scan a table whose encrypted columns `cipher` opens as rows are decoded.
    pub fn with_cipher<S: PageStore>(
        heap: &HeapFile<S>,
        types: Vec<ColumnType>,
        cipher: Option<Box<dyn ValueCipher>>,
    ) -> Result<Self> {
        let tuples: Vec<Vec<u8>> = heap.scan()?.into_iter().map(|(_, bytes)| bytes).collect();
        Ok(HeapScan {
            tuples: tuples.into_iter(),
            types,
            cipher,
        })
    }
}

impl RowSource for HeapScan {
    fn next_row(&mut self) -> Result<Option<Row>> {
        self.tuples
            .next()
            .map(|bytes| decode_row_with(&self.types, &bytes, self.cipher.as_deref()))
            .transpose()
    }
}

/// Rows whose indexed `i64` key lies in `[lo, hi]` (either side open), in physical order.
pub struct IndexScan {
    tuples: std::vec::IntoIter<Vec<u8>>,
    types: Vec<ColumnType>,
    cipher: Option<Box<dyn ValueCipher>>,
}

impl IndexScan {
    pub fn new<S: PageStore, T: PageStore>(
        heap: &HeapFile<S>,
        index: &BPlusTree<T>,
        lo: Option<i64>,
        hi: Option<i64>,
        types: Vec<ColumnType>,
    ) -> Result<Self> {
        Self::with_cipher(heap, index, lo, hi, types, None)
    }

    pub fn with_cipher<S: PageStore, T: PageStore>(
        heap: &HeapFile<S>,
        index: &BPlusTree<T>,
        lo: Option<i64>,
        hi: Option<i64>,
        types: Vec<ColumnType>,
        cipher: Option<Box<dyn ValueCipher>>,
    ) -> Result<Self> {
        let mut rids: Vec<Rid> = index.range(lo, hi)?.into_iter().map(|(_, r)| r).collect();
        // Physical order, so results match a sequential scan of the same rows.
        rids.sort();
        let mut tuples = Vec::with_capacity(rids.len());
        for rid in rids {
            tuples
                .push(heap.get(rid)?.ok_or_else(|| {
                    Error::engine(format!("index points at deleted tuple {rid}"))
                })?);
        }
        Ok(IndexScan {
            tuples: tuples.into_iter(),
            types,
            cipher,
        })
    }
}

impl RowSource for IndexScan {
    fn next_row(&mut self) -> Result<Option<Row>> {
        self.tuples
            .next()
            .map(|bytes| decode_row_with(&self.types, &bytes, self.cipher.as_deref()))
            .transpose()
    }
}

// ------------------------------------------------------------------- filter

/// A predicate resolved against a table: column position, operator, literal of the column's type.
#[derive(Debug, Clone, PartialEq)]
pub struct Predicate {
    pub column: usize,
    pub op: CmpOp,
    pub literal: Value,
}

impl Predicate {
    /// Resolve `filter` against `table`, coercing the literal to the column's type (timestamps
    /// arrive from the binder as text).
    pub fn compile(table: &TableDef, filter: &BoundFilter) -> Result<Self> {
        let (column, def) = table.column(&filter.column).ok_or_else(|| {
            Error::catalog(format!("unknown column: {}.{}", table.name, filter.column))
        })?;
        let literal = match (def.col_type, &filter.value) {
            (ColumnType::Timestamp, Value::Text(s)) => Value::Timestamp(parse_timestamp(s)?),
            (ColumnType::F64, Value::I64(v)) => Value::F64(*v as f64),
            (ColumnType::F64, Value::I32(v)) => Value::F64(f64::from(*v)),
            (_, v) => v.clone(),
        };
        Ok(Predicate {
            column,
            op: filter.op,
            literal,
        })
    }

    /// SQL semantics: `NULL` (or incomparable types) never matches, not even `<>`.
    pub fn matches(&self, row: &Row) -> bool {
        let Some(ord) = row.get(self.column).and_then(|v| compare(v, &self.literal)) else {
            return false;
        };
        match self.op {
            CmpOp::Eq => ord.is_eq(),
            CmpOp::Ne => ord.is_ne(),
            CmpOp::Gt => ord.is_gt(),
            CmpOp::Gte => ord.is_ge(),
            CmpOp::Lt => ord.is_lt(),
            CmpOp::Lte => ord.is_le(),
        }
    }
}

fn as_int(v: &Value) -> Option<i64> {
    match v {
        Value::I32(x) => Some(i64::from(*x)),
        Value::I64(x) | Value::Timestamp(x) => Some(*x),
        _ => None,
    }
}

/// Order two values of comparable types; `None` for `NULL` or a type mismatch.
fn compare(a: &Value, b: &Value) -> Option<Ordering> {
    match (a, b) {
        (Value::Null, _) | (_, Value::Null) => None,
        (Value::Text(x), Value::Text(y)) => Some(x.as_bytes().cmp(y.as_bytes())),
        (Value::Bool(x), Value::Bool(y)) => Some(x.cmp(y)),
        _ => match (as_int(a), as_int(b)) {
            (Some(x), Some(y)) => Some(x.cmp(&y)),
            _ => a.as_f64()?.partial_cmp(&b.as_f64()?),
        },
    }
}

/// Keeps rows satisfying every predicate.
pub struct Filter<S: RowSource> {
    input: S,
    predicates: Vec<Predicate>,
}

impl<S: RowSource> Filter<S> {
    pub fn new(input: S, predicates: Vec<Predicate>) -> Self {
        Filter { input, predicates }
    }
}

impl<S: RowSource> RowSource for Filter<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        while let Some(row) = self.input.next_row()? {
            if self.predicates.iter().all(|p| p.matches(&row)) {
                return Ok(Some(row));
            }
        }
        Ok(None)
    }
}

// --------------------------------------------------------------- aggregates

fn cell_value(row: &Row, idx: Option<usize>) -> Result<f64> {
    let Some(i) = idx else { return Ok(0.0) };
    match row.get(i) {
        Some(Value::Null) => Ok(0.0),
        Some(v) => v
            .as_f64()
            .ok_or_else(|| Error::engine(format!("cannot aggregate non-numeric value {v:?}"))),
        None => Err(Error::engine(format!("row has no column at position {i}"))),
    }
}

fn finish(agg: Agg, n: u64, total: f64) -> Value {
    match agg {
        Agg::Count => Value::I64(n as i64),
        Agg::Sum => Value::F64(total),
        Agg::Avg => Value::F64(if n == 0 { 0.0 } else { total / n as f64 }),
    }
}

/// Exact scalar aggregate: one output row `[value]`. `COUNT` yields `I64`, the others `F64`.
pub struct Aggregate<S: RowSource> {
    input: S,
    agg: Agg,
    value: Option<usize>,
    out: Option<Row>,
    done: bool,
}

impl<S: RowSource> Aggregate<S> {
    pub fn new(input: S, agg: Agg, value: Option<usize>) -> Self {
        Aggregate {
            input,
            agg,
            value,
            out: None,
            done: false,
        }
    }
}

impl<S: RowSource> RowSource for Aggregate<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        if !self.done {
            let (mut n, mut total) = (0u64, 0.0);
            while let Some(row) = self.input.next_row()? {
                n += 1;
                total += cell_value(&row, self.value)?;
            }
            self.out = Some(vec![finish(self.agg, n, total)]);
            self.done = true;
        }
        Ok(self.out.take())
    }
}

/// Exact grouped aggregate: one row `[group, value]` per group, ascending by group.
pub struct GroupBy<S: RowSource> {
    input: S,
    agg: Agg,
    value: Option<usize>,
    group: usize,
    out: Option<std::vec::IntoIter<Row>>,
}

impl<S: RowSource> GroupBy<S> {
    pub fn new(input: S, agg: Agg, value: Option<usize>, group: usize) -> Self {
        GroupBy {
            input,
            agg,
            value,
            group,
            out: None,
        }
    }
}

/// Total order on group keys: `NULL` first, then by type, then by value.
fn group_order(a: &Value, b: &Value) -> Ordering {
    let tag = |v: &Value| match v {
        Value::Null => 0,
        Value::Bool(_) => 1,
        Value::F64(_) => 3,
        Value::Text(_) => 4,
        _ => 2,
    };
    match (a, b) {
        (Value::F64(x), Value::F64(y)) => x.total_cmp(y),
        _ => tag(a)
            .cmp(&tag(b))
            .then_with(|| compare(a, b).unwrap_or(Ordering::Equal)),
    }
}

impl<S: RowSource> RowSource for GroupBy<S> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        if self.out.is_none() {
            let mut groups: Vec<(Value, u64, f64)> = Vec::new();
            while let Some(row) = self.input.next_row()? {
                let key = row
                    .get(self.group)
                    .cloned()
                    .ok_or_else(|| Error::engine("row has no group column"))?;
                let v = cell_value(&row, self.value)?;
                match groups
                    .iter_mut()
                    .find(|(k, _, _)| group_order(k, &key).is_eq())
                {
                    Some(g) => {
                        g.1 += 1;
                        g.2 += v;
                    }
                    None => groups.push((key, 1, v)),
                }
            }
            groups.sort_by(|a, b| group_order(&a.0, &b.0));
            let rows: Vec<Row> = groups
                .into_iter()
                .map(|(k, n, t)| vec![k, finish(self.agg, n, t)])
                .collect();
            self.out = Some(rows.into_iter());
        }
        Ok(self.out.as_mut().and_then(Iterator::next))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use privguard_catalog::ColumnDef;
    use privguard_core::VecRowSource;
    use privguard_storage::{encode_row, MemPageStore};

    fn table() -> TableDef {
        TableDef::new(
            "t",
            vec![
                ColumnDef::new("id", ColumnType::I64),
                ColumnDef::new("k", ColumnType::Text).groupable(),
                ColumnDef::new("x", ColumnType::F64).nullable(),
                ColumnDef::new("ts", ColumnType::Timestamp),
            ],
        )
    }

    fn row(id: i64, k: &str, x: Option<f64>, ts: i64) -> Row {
        vec![
            Value::I64(id),
            Value::Text(k.into()),
            x.map_or(Value::Null, Value::F64),
            Value::Timestamp(ts),
        ]
    }

    fn sample() -> Vec<Row> {
        vec![
            row(1, "a", Some(10.0), 0),
            row(2, "b", Some(20.0), 86_400_000_000),
            row(3, "a", None, 2 * 86_400_000_000),
            row(4, "b", Some(5.5), 3 * 86_400_000_000),
        ]
    }

    fn pred(column: &str, op: CmpOp, value: Value) -> Predicate {
        Predicate::compile(
            &table(),
            &BoundFilter {
                column: column.into(),
                op,
                value,
            },
        )
        .unwrap()
    }

    fn ids(rows: &[Row]) -> Vec<i64> {
        rows.iter().map(|r| as_int(&r[0]).unwrap()).collect()
    }

    fn run(preds: Vec<Predicate>) -> Vec<i64> {
        let mut f = Filter::new(VecRowSource::new(sample()), preds);
        ids(&f.collect_rows().unwrap())
    }

    #[test]
    fn filter_applies_every_operator() {
        assert_eq!(
            run(vec![pred("k", CmpOp::Eq, Value::Text("a".into()))]),
            [1, 3]
        );
        assert_eq!(
            run(vec![pred("k", CmpOp::Ne, Value::Text("a".into()))]),
            [2, 4]
        );
        assert_eq!(run(vec![pred("id", CmpOp::Gt, Value::I64(2))]), [3, 4]);
        assert_eq!(run(vec![pred("id", CmpOp::Gte, Value::I64(2))]), [2, 3, 4]);
        assert_eq!(run(vec![pred("id", CmpOp::Lt, Value::I64(2))]), [1]);
        assert_eq!(run(vec![pred("id", CmpOp::Lte, Value::I64(2))]), [1, 2]);
    }

    #[test]
    fn predicates_are_conjunctive() {
        let both = vec![
            pred("k", CmpOp::Eq, Value::Text("b".into())),
            pred("x", CmpOp::Gt, Value::F64(6.0)),
        ];
        assert_eq!(run(both), [2]);
    }

    #[test]
    fn null_never_matches_not_even_not_equal() {
        // Row 3 has x = NULL.
        assert_eq!(run(vec![pred("x", CmpOp::Ne, Value::F64(1.0))]), [1, 2, 4]);
        assert_eq!(run(vec![pred("x", CmpOp::Lt, Value::F64(1e9))]), [1, 2, 4]);
    }

    #[test]
    fn integer_literals_coerce_to_float_columns() {
        assert_eq!(run(vec![pred("x", CmpOp::Gte, Value::I64(10))]), [1, 2]);
    }

    #[test]
    fn timestamp_columns_accept_text_literals() {
        let p = pred("ts", CmpOp::Gte, Value::Text("1970-01-02 00:00:00".into()));
        assert_eq!(p.literal, Value::Timestamp(86_400_000_000));
        assert_eq!(run(vec![p]), [2, 3, 4]);
        let bad = Predicate::compile(
            &table(),
            &BoundFilter {
                column: "ts".into(),
                op: CmpOp::Eq,
                value: Value::Text("not a date".into()),
            },
        );
        assert!(bad.is_err());
    }

    #[test]
    fn unknown_columns_are_catalog_errors() {
        let err = Predicate::compile(
            &table(),
            &BoundFilter {
                column: "nope".into(),
                op: CmpOp::Eq,
                value: Value::I64(1),
            },
        )
        .unwrap_err();
        assert_eq!(err.sqlstate().code(), "42P01");
    }

    #[test]
    fn scalar_aggregates_treat_null_as_zero() {
        let agg = |a, v| {
            Aggregate::new(VecRowSource::new(sample()), a, v)
                .collect_rows()
                .unwrap()
        };
        assert_eq!(agg(Agg::Count, None), vec![vec![Value::I64(4)]]);
        assert_eq!(agg(Agg::Sum, Some(2)), vec![vec![Value::F64(35.5)]]);
        assert_eq!(agg(Agg::Avg, Some(2)), vec![vec![Value::F64(35.5 / 4.0)]]);
    }

    #[test]
    fn an_empty_input_still_yields_one_scalar_row() {
        let rows = Aggregate::new(VecRowSource::new(vec![]), Agg::Count, None)
            .collect_rows()
            .unwrap();
        assert_eq!(rows, vec![vec![Value::I64(0)]]);
        let rows = Aggregate::new(VecRowSource::new(vec![]), Agg::Avg, Some(2))
            .collect_rows()
            .unwrap();
        assert_eq!(rows, vec![vec![Value::F64(0.0)]]);
        // Fused after the single row.
        let mut a = Aggregate::new(VecRowSource::new(vec![]), Agg::Count, None);
        a.next_row().unwrap();
        assert_eq!(a.next_row().unwrap(), None);
    }

    #[test]
    fn group_by_is_sorted_by_key() {
        let rows = GroupBy::new(VecRowSource::new(sample()), Agg::Sum, Some(2), 1)
            .collect_rows()
            .unwrap();
        assert_eq!(
            rows,
            vec![
                vec![Value::Text("a".into()), Value::F64(10.0)],
                vec![Value::Text("b".into()), Value::F64(25.5)],
            ]
        );
        let counts = GroupBy::new(VecRowSource::new(sample()), Agg::Count, None, 1)
            .collect_rows()
            .unwrap();
        assert_eq!(counts[0][1], Value::I64(2));
    }

    #[test]
    fn heap_and_index_scans_agree() {
        let types = table().column_types();
        let mut heap = HeapFile::open(MemPageStore::new()).unwrap();
        let mut index = BPlusTree::open(MemPageStore::new()).unwrap();
        for r in sample() {
            let rid = heap.insert(&encode_row(&types, &r).unwrap()).unwrap();
            if let Value::I64(id) = r[0] {
                index.insert(id, rid).unwrap();
            }
        }
        let all = HeapScan::new(&heap, types.clone())
            .unwrap()
            .collect_rows()
            .unwrap();
        assert_eq!(all, sample());

        let some = IndexScan::new(&heap, &index, Some(2), Some(3), types.clone())
            .unwrap()
            .collect_rows()
            .unwrap();
        assert_eq!(ids(&some), [2, 3]);
        let open = IndexScan::new(&heap, &index, None, None, types)
            .unwrap()
            .collect_rows()
            .unwrap();
        assert_eq!(open, all);
    }
}
