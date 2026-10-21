#![allow(dead_code)]
//! The engine facade end to end: golden dataset -> loader -> heap files -> `Engine::query`.
//!
//! Using the oracle's golden secret and principal id 1 means the engine's seed equals the
//! fixture's seed, so its answers must equal the oracle's committed noisy answers exactly.

use privguard_catalog::load_safehealth;
use privguard_core::json::{self, Json};
use privguardd::engine::{seed_demo, Engine};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};

pub const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../oracle/tests/golden");
pub const GOLDEN_SECRET: &[u8] = b"privguard-golden-secret-v1";

pub fn temp_dir(tag: &str) -> PathBuf {
    static N: AtomicU32 = AtomicU32::new(0);
    let d = std::env::temp_dir().join(format!(
        "privguard-{tag}-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

pub fn fixture(name: &str) -> Json {
    json::parse(&std::fs::read_to_string(format!("{GOLDEN}/{name}")).unwrap()).unwrap()
}

/// Write the golden dataset as the CSV set the loader reads (`visits` real, the rest empty).
pub fn write_csvs(dir: &Path) {
    let headers = [
        ("regions", "region_id,region_name,state_code"),
        ("hospitals", "hospital_id,region_id,hospital_name,bed_count"),
        ("departments", "dept_id,hospital_id,dept_name,specialty"),
        ("staff", "staff_id,dept_id,full_name,staff_role,hire_date"),
        (
            "patients",
            "patient_id,region_id,mrn,birth_year,sex,insurance_type,enrolled_at",
        ),
        (
            "diagnoses",
            "diagnosis_id,visit_id,icd10_code,is_primary,severity",
        ),
        (
            "lab_results",
            "lab_id,visit_id,test_code,value_num,unit,taken_at",
        ),
    ];
    for (name, header) in headers {
        std::fs::write(dir.join(format!("{name}.csv")), format!("{header}\n")).unwrap();
    }
    let mut visits = String::from(
        "visit_id,patient_id,dept_id,attending_staff_id,admit_ts,discharge_ts,total_charge,admission_type\n",
    );
    for r in fixture("dataset.json").as_array().unwrap() {
        let f = |k: &str| match r.get(k).unwrap() {
            Json::Null => String::new(),
            Json::Str(s) => s.clone(),
            Json::Num(n, true) => (*n as i64).to_string(),
            Json::Num(n, false) => format!("{n:?}"),
            other => panic!("{other:?}"),
        };
        let cols = [
            "visit_id",
            "patient_id",
            "dept_id",
            "attending_staff_id",
            "admit_ts",
            "discharge_ts",
            "total_charge",
            "admission_type",
        ];
        visits.push_str(&cols.map(f).join(","));
        visits.push('\n');
    }
    std::fs::write(dir.join("visits.csv"), visits).unwrap();
}

/// A loaded database with the demo principals. alice is principal 1.
pub fn database() -> PathBuf {
    let root = temp_dir("engine");
    let csv = root.join("csv");
    std::fs::create_dir_all(&csv).unwrap();
    write_csvs(&csv);
    let db = root.join("db");
    load_safehealth(&csv, &db).unwrap();
    let mut e = Engine::open(&db, GOLDEN_SECRET).unwrap();
    seed_demo(&mut e).unwrap();
    db
}

pub fn open(db: &Path) -> Engine {
    Engine::open(db, GOLDEN_SECRET).unwrap()
}

pub fn expected(id: &str) -> Json {
    let f = fixture("queries.json");
    f.get("queries")
        .unwrap()
        .as_array()
        .unwrap()
        .iter()
        .find(|q| q.get("id").unwrap().as_str() == Some(id))
        .unwrap()
        .get("expected")
        .unwrap()
        .clone()
}
