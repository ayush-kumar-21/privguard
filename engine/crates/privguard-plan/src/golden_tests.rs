//! The executor against the oracle, through real heap and index files.
//!
//! The oracle's golden dataset is loaded into on-disk heap files, then:
//! - the exact plan must equal the oracle's non-private aggregates (`baseline.json`, 2.10);
//! - the DP plan must equal the oracle's noisy answers (`queries.json`), which proves the
//!   operators behave the same when fed by the storage layer instead of in-memory rows.
//!
//! Integers must match exactly. Floats may differ by one rounding step: the noise streams are
//! bit-exact, but `ln`/`cos` come from the platform libm.

use crate::build::{build_dp, build_exact, catalog_lookup};
use privguard_catalog::loader::{create_safehealth_catalog, parse_timestamp};
use privguard_catalog::Catalog;
use privguard_core::json::{self, Json};
use privguard_core::{Row, RowSource, Value};
use privguard_sql::{parse_and_bind, Agg, BoundFilter, BoundQuery, CmpOp};
use privguard_storage::{encode_row, BPlusTree, FilePageStore, HeapFile};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../oracle/tests/golden");

fn fixture(name: &str) -> Json {
    let path = format!("{GOLDEN}/{name}");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("cannot read golden fixture {path}: {e}"));
    json::parse(&text).unwrap()
}

fn temp_dir() -> PathBuf {
    static N: AtomicU32 = AtomicU32::new(0);
    std::env::temp_dir().join(format!(
        "privguard-plan-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ))
}

/// A catalog whose `visits` heap holds the golden dataset, with the loader's two indexes.
fn golden_catalog() -> (Catalog, PathBuf) {
    let dir = temp_dir();
    let _ = std::fs::remove_dir_all(&dir);
    let cat = create_safehealth_catalog(&dir).unwrap();
    let visits = cat.table("visits").unwrap().clone();
    let types = visits.column_types();

    let mut heap =
        HeapFile::open(FilePageStore::create(&cat.heap_path("visits")).unwrap()).unwrap();
    let mut by_id =
        BPlusTree::open(FilePageStore::create(&cat.index_path("visits", "visit_id")).unwrap())
            .unwrap();
    let mut by_patient =
        BPlusTree::open(FilePageStore::create(&cat.index_path("visits", "patient_id")).unwrap())
            .unwrap();

    for r in fixture("dataset.json").as_array().unwrap() {
        let int = |k: &str| Value::I64(r.get(k).unwrap().as_i64().unwrap());
        let ts = |k: &str| {
            Value::Timestamp(parse_timestamp(r.get(k).unwrap().as_str().unwrap()).unwrap())
        };
        let row: Row = vec![
            int("visit_id"),
            int("patient_id"),
            int("dept_id"),
            int("attending_staff_id"),
            ts("admit_ts"),
            ts("discharge_ts"),
            r.get("total_charge").unwrap().to_value().unwrap(),
            r.get("admission_type").unwrap().to_value().unwrap(),
        ];
        let rid = heap.insert(&encode_row(&types, &row).unwrap()).unwrap();
        for (tree, col) in [(&mut by_id, 0), (&mut by_patient, 1)] {
            if let Value::I64(k) = row[col] {
                tree.insert(k, rid).unwrap();
            }
        }
    }
    (cat, dir)
}

fn op_of(name: &str) -> CmpOp {
    match name {
        "exact" => CmpOp::Eq,
        "ne" => CmpOp::Ne,
        "gt" => CmpOp::Gt,
        "gte" => CmpOp::Gte,
        "lt" => CmpOp::Lt,
        "lte" => CmpOp::Lte,
        other => panic!("operator {other}"),
    }
}

/// The fixture entry as the binder would have produced it.
fn bound(q: &Json) -> BoundQuery {
    let text = |k: &str| q.get(k).unwrap().as_str().map(str::to_string);
    let agg = match text("agg").unwrap().as_str() {
        "COUNT" => Agg::Count,
        "SUM" => Agg::Sum,
        "AVG" => Agg::Avg,
        other => panic!("agg {other}"),
    };
    let table = q.get("table_policy").unwrap();
    let filters = q
        .get("filters")
        .unwrap()
        .as_array()
        .unwrap()
        .iter()
        .map(|f| {
            let f = f.as_array().unwrap();
            let value = f[2].to_value().unwrap();
            BoundFilter {
                column: f[0].as_str().unwrap().to_string(),
                op: op_of(f[1].as_str().unwrap()),
                value,
            }
        })
        .collect();
    let clamp_bounds = q.get("column_policy").and_then(|p| {
        (!p.is_null()).then(|| {
            (
                p.get("lower_clamp").unwrap().as_f64().unwrap(),
                p.get("upper_clamp").unwrap().as_f64().unwrap(),
            )
        })
    });
    BoundQuery {
        agg,
        column: text("column"),
        clamp_bounds,
        table: "visits".into(),
        max_rows_per_unit: table.get("max_rows_per_unit").unwrap().as_f64().unwrap() as u32,
        max_groups_per_unit: table.get("max_groups_per_unit").unwrap().as_f64().unwrap() as u32,
        filters,
        group_by: text("group_by"),
    }
}

fn hex(s: &str) -> Vec<u8> {
    (0..s.len() / 2)
        .map(|i| u8::from_str_radix(&s[2 * i..2 * i + 2], 16).unwrap())
        .collect()
}

fn close(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol * (1.0 + b.abs()) || (a - b).abs() <= tol
}

/// Compare engine rows to fixture rows (`[{"value": v}]` or `[{group_col: k, "value": v}]`).
fn assert_rows(id: &str, got: &[Row], want: &[Json], group_col: Option<&str>, tol: f64) {
    assert_eq!(got.len(), want.len(), "{id}: row count");
    for (g, w) in got.iter().zip(want) {
        let value = g.last().unwrap();
        if let Some(col) = group_col {
            assert_eq!(
                g[0],
                w.get(col).unwrap().to_value().unwrap(),
                "{id}: group key"
            );
        }
        match (value, w.get("value").unwrap()) {
            (Value::I64(a), Json::Num(b, true)) => assert_eq!(*a, *b as i64, "{id}: value"),
            (Value::F64(a), Json::Num(b, _)) => {
                assert!(close(*a, *b, tol), "{id}: value {a} vs {b}")
            }
            other => panic!("{id}: value types differ: {other:?}"),
        }
    }
}

#[test]
fn exact_plan_matches_the_oracle_baseline_for_every_golden_query() {
    let (cat, dir) = golden_catalog();
    let queries = fixture("queries.json");
    let baseline = fixture("baseline.json");
    let queries = queries.get("queries").unwrap().as_array().unwrap();
    let baseline = baseline.get("queries").unwrap().as_array().unwrap();
    assert_eq!(queries.len(), baseline.len());

    for (q, b) in queries.iter().zip(baseline) {
        let id = q.get("id").unwrap().as_str().unwrap();
        assert_eq!(b.get("id").unwrap().as_str().unwrap(), id);
        let query = bound(q);
        let rows = build_exact(&cat, &query).unwrap().collect_rows().unwrap();
        assert_rows(
            id,
            &rows,
            b.get("rows").unwrap().as_array().unwrap(),
            query.group_by.as_deref(),
            1e-9,
        );
    }
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn dp_plan_matches_the_oracle_noisy_answers_for_every_golden_query() {
    let (cat, dir) = golden_catalog();
    let queries = fixture("queries.json");
    let queries = queries.get("queries").unwrap().as_array().unwrap();
    assert_eq!(queries.len(), 20);

    let mut suppressed_total = 0;
    for q in queries {
        let id = q.get("id").unwrap().as_str().unwrap();
        let query = bound(q);
        let rho = q.get("rho").unwrap().as_f64().unwrap();
        let seed = hex(q.get("seed_hex").unwrap().as_str().unwrap());

        let mut plan = build_dp(&cat, &query, rho, &seed).unwrap();
        let rows = plan.source.collect_rows().unwrap();
        let want = q.get("expected").unwrap();

        assert_eq!(
            plan.calibration.mechanism,
            want.get("mechanism").unwrap().as_str().unwrap(),
            "{id}"
        );
        assert!(
            close(
                plan.calibration.scale,
                want.get("scale").unwrap().as_f64().unwrap(),
                1e-12
            ),
            "{id}: scale"
        );
        let suppressed = plan
            .suppressed
            .as_ref()
            .map_or(0, |c| c.load(Ordering::Relaxed));
        assert_eq!(
            suppressed as f64,
            want.get("suppressed_groups").unwrap().as_f64().unwrap(),
            "{id}: suppressed"
        );
        suppressed_total += suppressed;
        assert_rows(
            id,
            &rows,
            want.get("rows").unwrap().as_array().unwrap(),
            query.group_by.as_deref(),
            1.01e-4,
        );
    }
    assert!(suppressed_total > 0);
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn sql_text_runs_through_the_binder_and_the_exact_plan() {
    let (cat, dir) = golden_catalog();
    let lookup = catalog_lookup(&cat);
    let run = |sql: &str| {
        let q = parse_and_bind(sql, &lookup).unwrap();
        build_exact(&cat, &q).unwrap().collect_rows().unwrap()
    };
    assert_eq!(
        run("SELECT COUNT(*) FROM visits"),
        vec![vec![Value::I64(211)]]
    );
    assert_eq!(
        run("SELECT COUNT(*) FROM visits WHERE admit_ts >= '2024-03-01 00:00:00' AND admit_ts < '2024-09-01 00:00:00'"),
        vec![vec![Value::I64(108)]]
    );
    let grouped = run("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type");
    let total: i64 = grouped
        .iter()
        .map(|r| match r[1] {
            Value::I64(n) => n,
            _ => panic!(),
        })
        .sum();
    assert_eq!(total, 211);
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn an_indexed_predicate_returns_the_same_rows_as_a_full_scan() {
    let (cat, dir) = golden_catalog();
    let mut q = bound(
        &fixture("queries.json")
            .get("queries")
            .unwrap()
            .as_array()
            .unwrap()[0],
    );
    // patient_id is indexed; the binder forbids filtering on it, so build the filter by hand.
    q.filters = vec![BoundFilter {
        column: "patient_id".into(),
        op: CmpOp::Eq,
        value: Value::I64(1),
    }];
    let via_index = build_exact(&cat, &q).unwrap().collect_rows().unwrap();
    assert_eq!(
        via_index,
        vec![vec![Value::I64(8)]],
        "patient 1 has 8 visits"
    );

    q.filters = vec![
        BoundFilter {
            column: "visit_id".into(),
            op: CmpOp::Gte,
            value: Value::I64(10),
        },
        BoundFilter {
            column: "visit_id".into(),
            op: CmpOp::Lt,
            value: Value::I64(20),
        },
    ];
    let rows = build_exact(&cat, &q).unwrap().collect_rows().unwrap();
    assert_eq!(rows, vec![vec![Value::I64(10)]]);
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn a_dp_plan_without_clamp_bounds_is_refused_by_policy() {
    let (cat, dir) = golden_catalog();
    let mut q = bound(
        &fixture("queries.json")
            .get("queries")
            .unwrap()
            .as_array()
            .unwrap()[5],
    );
    assert_eq!(q.agg, Agg::Sum);
    q.clamp_bounds = None;
    let err = build_dp(&cat, &q, 0.5, b"s").err().unwrap();
    assert_eq!(err.sqlstate().code(), "42501");
    std::fs::remove_dir_all(dir).unwrap();
}
