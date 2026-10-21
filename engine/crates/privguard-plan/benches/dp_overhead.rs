//! Phase 5.1: what does differential privacy cost over the non-private executor?
//!
//! Each query runs twice over the same heap files: `exact` (Scan -> Filter -> Aggregate) and `dp`
//! (Scan -> Filter -> ContributionBound -> [PartitionSelect] -> DPAggregate). The ratio is the
//! overhead of the privacy operators. `cargo bench -p privguard-plan`.
//!
//! `end_to_end` includes building the plan, which for `HeapScan` reads the whole heap eagerly;
//! `execute_only` builds outside the timer, so it isolates the operators.

use criterion::{criterion_group, criterion_main, BatchSize, Criterion, Throughput};
use privguard_catalog::loader::{create_safehealth_catalog, parse_timestamp};
use privguard_catalog::Catalog;
use privguard_core::{Row, RowSource, Value};
use privguard_plan::build::{build_dp, build_exact, catalog_lookup};
use privguard_sql::parse_and_bind;
use privguard_storage::{encode_row, FilePageStore, HeapFile};
use std::path::PathBuf;

const ROWS: i64 = 20_000;
const TYPES: [&str; 4] = ["ELECTIVE", "EMERGENCY", "URGENT", "TRAUMA"];

fn load() -> (Catalog, PathBuf) {
    let dir = std::env::temp_dir().join(format!("privguard-bench-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let cat = create_safehealth_catalog(&dir).unwrap();
    let types = cat.table("visits").unwrap().column_types();
    let mut heap =
        HeapFile::open(FilePageStore::create(&cat.heap_path("visits")).unwrap()).unwrap();
    let ts = parse_timestamp("2024-05-01 08:00:00").unwrap();
    for i in 1..=ROWS {
        let row: Row = vec![
            Value::I64(i),
            Value::I64(1 + i % 4_000), // 4,000 patients, ~5 visits each
            Value::I64(1 + i % 6),
            Value::I64(1 + i % 50),
            Value::Timestamp(ts),
            Value::Timestamp(ts),
            Value::F64(50.0 + ((i * 37) % 1_400) as f64),
            Value::Text(TYPES[(i % 4) as usize].into()),
        ];
        heap.insert(&encode_row(&types, &row).unwrap()).unwrap();
    }
    (cat, dir)
}

fn bench(c: &mut Criterion) {
    let (cat, dir) = load();
    let lookup = catalog_lookup(&cat);
    let queries = [
        ("count", "SELECT COUNT(*) FROM visits"),
        (
            "sum",
            "SELECT SUM(total_charge) FROM visits WHERE dept_id = 2",
        ),
        (
            "grouped_count",
            "SELECT COUNT(*) FROM visits GROUP BY admission_type",
        ),
    ];
    for (name, sql) in queries {
        let mut q = parse_and_bind(sql, &lookup).unwrap();
        if q.column.is_some() {
            q.clamp_bounds = Some((0.0, 1500.0));
        }
        // end_to_end: build the plan (HeapScan reads the whole heap here) and drain it.
        let mut g = c.benchmark_group(format!("{name}/end_to_end"));
        g.throughput(Throughput::Elements(ROWS as u64));
        g.sample_size(20);
        g.bench_function("exact", |b| {
            b.iter(|| build_exact(&cat, &q).unwrap().collect_rows().unwrap())
        });
        g.bench_function("dp", |b| {
            b.iter(|| {
                build_dp(&cat, &q, 0.5, b"bench-seed")
                    .unwrap()
                    .source
                    .collect_rows()
                    .unwrap()
            })
        });
        g.finish();

        // execute_only: plan already built, so this is the operators on in-memory tuples.
        let mut g = c.benchmark_group(format!("{name}/execute_only"));
        g.throughput(Throughput::Elements(ROWS as u64));
        g.sample_size(20);
        g.bench_function("exact", |b| {
            b.iter_batched(
                || build_exact(&cat, &q).unwrap(),
                |mut plan| plan.collect_rows().unwrap(),
                BatchSize::LargeInput,
            )
        });
        g.bench_function("dp", |b| {
            b.iter_batched(
                || build_dp(&cat, &q, 0.5, b"bench-seed").unwrap(),
                |mut plan| plan.source.collect_rows().unwrap(),
                BatchSize::LargeInput,
            )
        });
        g.finish();
    }
    let _ = std::fs::remove_dir_all(dir);
}

criterion_group!(benches, bench);
criterion_main!(benches);
