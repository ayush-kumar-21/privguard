//! Loader: the synthetic SafeHealth dataset -> heap files + B+ tree indexes.
//! `IMPLEMENTATION_PLAN.md` 2.9.
//!
//! Input is the CSV set written by `oracle/privguard_oracle/dataset.py`
//! (`./run.sh data` -> `data/safehealth/*.csv`). Output is a catalog directory holding the
//! `sys_*` tables, one `<table>.heap` per SafeHealth table, and `<table>.<column>.idx` indexes
//! on every primary key and on `visits.patient_id`.
//!
//! The CSV reader is deliberately small: comma separated, optional double quotes with `""`
//! escapes, no newlines inside quoted fields (the generator never produces them).

use crate::catalog::{Catalog, ColumnDef, ColumnPrivacy, TableDef};
use crate::AccessMode;
use privguard_core::{ColumnType, Error, Result, Value};
use privguard_crypto::Keyring;
use privguard_storage::{encode_row_with, BPlusTree, FilePageStore, HeapFile, ValueCipher};
use std::path::Path;

/// Tables in load order (dimensions first), matching `dataset.TABLES` in the oracle.
pub const SAFEHEALTH_TABLES: [&str; 8] = [
    "regions",
    "hospitals",
    "departments",
    "staff",
    "patients",
    "visits",
    "diagnoses",
    "lab_results",
];

fn col(name: &str, t: ColumnType) -> ColumnDef {
    ColumnDef::new(name, t)
}

/// The `SPEC.md` §2.2 schema with the policies of `SPEC.md` §3 on the fact table.
pub fn safehealth_tables() -> Vec<TableDef> {
    use ColumnType::{Bool, Text, Timestamp, F64, I32, I64};
    let mut visits = TableDef::new(
        "visits",
        vec![
            col("visit_id", I64),
            col("patient_id", I64),
            col("dept_id", I64).filterable().groupable(),
            col("attending_staff_id", I64),
            col("admit_ts", Timestamp).filterable(),
            col("discharge_ts", Timestamp).filterable(),
            col("total_charge", F64).nullable().filterable(),
            col("admission_type", Text).filterable().groupable(),
        ],
    );
    visits.is_fact = true;

    vec![
        TableDef::new(
            "regions",
            vec![
                col("region_id", I64),
                col("region_name", Text),
                col("state_code", Text),
            ],
        ),
        TableDef::new(
            "hospitals",
            vec![
                col("hospital_id", I64),
                col("region_id", I64),
                col("hospital_name", Text),
                col("bed_count", I64),
            ],
        ),
        TableDef::new(
            "departments",
            vec![
                col("dept_id", I64),
                col("hospital_id", I64),
                col("dept_name", Text),
                col("specialty", Text),
            ],
        ),
        TableDef::new(
            "staff",
            vec![
                col("staff_id", I64),
                col("dept_id", I64),
                col("full_name", Text),
                col("staff_role", Text),
                col("hire_date", Text),
            ],
        ),
        TableDef::new(
            "patients",
            vec![
                col("patient_id", I64),
                col("region_id", I64),
                col("mrn", Text),
                col("birth_year", I32),
                col("sex", Text),
                col("insurance_type", Text),
                col("enrolled_at", Text),
            ],
        ),
        visits,
        TableDef::new(
            "diagnoses",
            vec![
                col("diagnosis_id", I64),
                col("visit_id", I64),
                col("icd10_code", Text),
                col("is_primary", Bool),
                col("severity", I32),
            ],
        ),
        TableDef::new(
            "lab_results",
            vec![
                col("lab_id", I64),
                col("visit_id", I64),
                col("test_code", Text),
                col("value_num", F64),
                col("unit", Text),
                col("taken_at", Timestamp),
            ],
        ),
    ]
}

/// Policy rows for `visits` and `patients`, mirroring `MockCatalog::safehealth()` in
/// `privguard-sql`. Tables and columns with no row stay `Denied`.
fn apply_policies(cat: &mut Catalog) -> Result<()> {
    let p = |access, clamp: Option<(f64, f64)>| ColumnPrivacy {
        access,
        mechanism: clamp.map(|_| "laplace".to_string()),
        clamp,
    };
    cat.set_table_privacy("visits", 5, 3)?;
    for (column, policy) in [
        ("visit_id", p(AccessMode::AggregateOnly, None)),
        ("patient_id", p(AccessMode::Denied, None)),
        ("dept_id", p(AccessMode::Allowed, None)),
        ("attending_staff_id", p(AccessMode::AggregateOnly, None)),
        ("admit_ts", p(AccessMode::Allowed, None)),
        ("discharge_ts", p(AccessMode::Allowed, None)),
        (
            "total_charge",
            p(AccessMode::AggregateOnly, Some((0.0, 1000.0))),
        ),
        ("admission_type", p(AccessMode::Allowed, None)),
    ] {
        cat.set_column_privacy("visits", column, policy)?;
    }
    cat.set_column_privacy("patients", "patient_id", p(AccessMode::Denied, None))?;
    cat.set_column_privacy("patients", "mrn", p(AccessMode::Denied, None))?;
    Ok(())
}

/// Split one CSV line into fields.
fn split_csv_line(line: &str) -> Result<Vec<String>> {
    let mut fields = Vec::new();
    let mut cur = String::new();
    let mut chars = line.chars().peekable();
    let mut in_quotes = false;
    while let Some(c) = chars.next() {
        match (c, in_quotes) {
            ('"', true) if chars.peek() == Some(&'"') => {
                cur.push('"');
                chars.next();
            }
            ('"', _) => in_quotes = !in_quotes,
            (',', false) => fields.push(std::mem::take(&mut cur)),
            (c, _) => cur.push(c),
        }
    }
    if in_quotes {
        return Err(Error::engine(format!(
            "unterminated quote in CSV line: {line}"
        )));
    }
    fields.push(cur);
    Ok(fields)
}

/// Days since 1970-01-01 of a proleptic Gregorian date (Howard Hinnant's algorithm).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// `YYYY-MM-DD[ T]HH:MM:SS[.ffffff]` or a bare date, to microseconds since the Unix epoch.
pub fn parse_timestamp(s: &str) -> Result<i64> {
    let bad = || Error::engine(format!("invalid timestamp: {s:?}"));
    let (date, time) = match s.split_once([' ', 'T']) {
        Some((d, t)) => (d, t),
        None => (s, "00:00:00"),
    };
    let mut d = date.split('-');
    let num = |it: &mut std::str::Split<'_, char>| -> Result<i64> {
        it.next().ok_or_else(bad)?.parse::<i64>().map_err(|_| bad())
    };
    let (y, mo, da) = (num(&mut d)?, num(&mut d)?, num(&mut d)?);
    if d.next().is_some() || !(1..=12).contains(&mo) || !(1..=31).contains(&da) {
        return Err(bad());
    }
    let (hms, frac) = time.split_once('.').unwrap_or((time, ""));
    let mut t = hms.split(':');
    let (h, mi, se) = (num(&mut t)?, num(&mut t)?, num(&mut t)?);
    if t.next().is_some() || h > 23 || mi > 59 || se > 60 {
        return Err(bad());
    }
    let micros = if frac.is_empty() {
        0
    } else {
        let digits: String = frac.chars().take(6).collect();
        let scale = 10i64.pow(6 - digits.len() as u32);
        digits.parse::<i64>().map_err(|_| bad())? * scale
    };
    Ok(((days_from_civil(y, mo, da) * 24 + h) * 60 + mi) * 60_000_000 + se * 1_000_000 + micros)
}

fn parse_field(def: &ColumnDef, raw: &str) -> Result<Value> {
    if raw.is_empty() && def.col_type != ColumnType::Text {
        return if def.nullable {
            Ok(Value::Null)
        } else {
            Err(Error::engine(format!(
                "empty value for non-null column {}",
                def.name
            )))
        };
    }
    let bad = |what: &str| Error::engine(format!("column {}: {raw:?} is not {what}", def.name));
    Ok(match def.col_type {
        ColumnType::I32 => Value::I32(raw.parse().map_err(|_| bad("an i32"))?),
        ColumnType::I64 => Value::I64(raw.parse().map_err(|_| bad("an i64"))?),
        ColumnType::F64 => Value::F64(raw.parse().map_err(|_| bad("a float"))?),
        ColumnType::Text => Value::Text(raw.to_string()),
        ColumnType::Timestamp => Value::Timestamp(parse_timestamp(raw)?),
        ColumnType::Bool => match raw {
            "True" | "true" | "1" | "t" => Value::Bool(true),
            "False" | "false" | "0" | "f" => Value::Bool(false),
            _ => return Err(bad("a bool")),
        },
    })
}

/// How many rows each table received.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadReport {
    pub rows: Vec<(String, u64)>,
}

/// Columns indexed for `table`: its primary key (first column) and, for `visits`, the
/// privacy-unit column.
fn indexed_columns(table: &str) -> Vec<usize> {
    match table {
        "visits" => vec![0, 1],
        _ => vec![0],
    }
}

fn load_table(cat: &Catalog, def: &TableDef, csv: &Path) -> Result<u64> {
    let text = std::fs::read_to_string(csv)
        .map_err(|e| Error::engine(format!("cannot read {}: {e}", csv.display())))?;
    let mut lines = text.lines().map(|l| l.trim_end_matches('\r'));
    let header = split_csv_line(lines.next().unwrap_or(""))?;
    // Map schema column -> CSV position by name, so column order in the file does not matter.
    let positions: Vec<usize> = def
        .columns
        .iter()
        .map(|c| {
            header.iter().position(|h| *h == c.name).ok_or_else(|| {
                Error::engine(format!("{} is missing column {}", csv.display(), c.name))
            })
        })
        .collect::<Result<_>>()?;

    let types = def.column_types();
    let cipher = cat.table_cipher(&def.name)?;
    let mut heap = HeapFile::open(FilePageStore::create(&cat.heap_path(&def.name))?)?;
    let mut indexes = Vec::new();
    for ord in indexed_columns(&def.name) {
        let name = &def.columns[ord].name;
        let store = FilePageStore::create(&cat.index_path(&def.name, name))?;
        indexes.push((ord, BPlusTree::open(store)?));
    }

    let mut n = 0u64;
    for (lineno, line) in lines.enumerate().filter(|(_, l)| !l.is_empty()) {
        let fields = split_csv_line(line)?;
        let row = def
            .columns
            .iter()
            .zip(&positions)
            .map(|(c, &p)| {
                let raw = fields.get(p).map_or("", String::as_str);
                parse_field(c, raw)
            })
            .collect::<Result<Vec<_>>>()
            .map_err(|e| Error::engine(format!("{} line {}: {e}", csv.display(), lineno + 2)))?;
        let rid = heap.insert(&encode_row_with(
            &types,
            &row,
            cipher.as_ref().map(|c| c as &dyn ValueCipher),
        )?)?;
        for (ord, tree) in &mut indexes {
            match row[*ord] {
                Value::I64(k) => tree.insert(k, rid)?,
                _ => return Err(Error::engine("indexed column must be a non-null i64")),
            };
        }
        n += 1;
    }
    Ok(n)
}

/// A fresh catalog in `dir` holding the SafeHealth schema and policies, with empty tables.
pub fn create_safehealth_catalog(dir: &Path) -> Result<Catalog> {
    create_safehealth_catalog_with(dir, None)
}

/// Like [`create_safehealth_catalog`]. With a `keyring`, `patients.mrn` is stored encrypted
/// (`SPEC.md` §5.5); without one it stays plaintext, and the catalog says so.
pub fn create_safehealth_catalog_with(dir: &Path, keyring: Option<Keyring>) -> Result<Catalog> {
    let encrypt = keyring.is_some();
    let mut cat = Catalog::create(dir, "safehealth", 1e-6)?;
    cat.set_keyring(keyring);
    for mut def in safehealth_tables() {
        if encrypt && def.name == "patients" {
            for c in def.columns.iter_mut().filter(|c| c.name == "mrn") {
                c.encrypted = true;
            }
        }
        cat.add_table(def)?;
    }
    apply_policies(&mut cat)?;
    Ok(cat)
}

/// Load every SafeHealth CSV in `csv_dir` into a fresh catalog directory `catalog_dir`.
pub fn load_safehealth(csv_dir: &Path, catalog_dir: &Path) -> Result<(Catalog, LoadReport)> {
    load_safehealth_with(csv_dir, catalog_dir, None)
}

/// Like [`load_safehealth`], encrypting the columns the key covers.
pub fn load_safehealth_with(
    csv_dir: &Path,
    catalog_dir: &Path,
    keyring: Option<Keyring>,
) -> Result<(Catalog, LoadReport)> {
    let mut cat = create_safehealth_catalog_with(catalog_dir, keyring)?;

    let mut report = LoadReport { rows: Vec::new() };
    for name in SAFEHEALTH_TABLES {
        let def = cat.table(name).expect("added above").clone();
        let n = load_table(&cat, &def, &csv_dir.join(format!("{name}.csv")))?;
        cat.set_row_estimate(name, n as i64)?;
        report.rows.push((name.to_string(), n));
    }
    cat.save()?;
    Ok((cat, report))
}

impl Catalog {
    /// Open the heap file of user table `table`.
    pub fn open_heap(&self, table: &str) -> Result<HeapFile<FilePageStore>> {
        if self.table(table).is_none() {
            return Err(Error::catalog(format!("unknown table: {table}")));
        }
        HeapFile::open(FilePageStore::open(&self.heap_path(table))?)
    }

    /// Open the B+ tree index on `table.column`.
    pub fn open_index(&self, table: &str, column: &str) -> Result<BPlusTree<FilePageStore>> {
        let path = self.index_path(table, column);
        if !path.exists() {
            return Err(Error::catalog(format!("no index on {table}.{column}")));
        }
        BPlusTree::open(FilePageStore::open(&path)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::TablePrivacy;
    use privguard_storage::decode_row;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};

    fn temp_dir(tag: &str) -> PathBuf {
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

    /// A miniature dataset in the exact shape `dataset.py` writes.
    fn write_sample_csvs(dir: &Path) {
        let files = [
            ("regions", "region_id,region_name,state_code\n1,North,TN\n2,\"South, East\",KL\n"),
            ("hospitals", "hospital_id,region_id,hospital_name,bed_count\n1,1,SafeHealth 01,120\n"),
            ("departments", "dept_id,hospital_id,dept_name,specialty\n1,1,Cardiology,cardiology\n2,1,Oncology,oncology\n"),
            ("staff", "staff_id,dept_id,full_name,staff_role,hire_date\n1,1,Staff 001,nurse,2016-03-04\n"),
            ("patients", "patient_id,region_id,mrn,birth_year,sex,insurance_type,enrolled_at\n1,1,MRN000001,1980,F,public,2021-01-01\n2,2,MRN000002,1975,M,private,2022-02-02\n3,1,MRN000003,1999,X,self-pay,2023-03-03\n"),
            (
                "visits",
                "visit_id,patient_id,dept_id,attending_staff_id,admit_ts,discharge_ts,total_charge,admission_type\r\n\
                 1,1,1,1,2024-01-03 05:07:00,2024-01-04 09:07:00,1200.5,EMERGENCY\r\n\
                 2,1,2,1,2024-02-03 05:07:00,2024-02-03 09:07:00,310.25,ELECTIVE\r\n\
                 3,2,1,1,1970-01-02 00:00:00,1970-01-02 01:00:00,99.99,URGENT\r\n\
                 4,3,2,1,2024-02-29 23:59:59,2024-03-01 00:00:00,10,ELECTIVE\r\n",
            ),
            ("diagnoses", "diagnosis_id,visit_id,icd10_code,is_primary,severity\n1,1,I21.9,True,3\n2,1,E11.9,False,1\n"),
            ("lab_results", "lab_id,visit_id,test_code,value_num,unit,taken_at\n1,1,GLU,101.5,mg/dL,2024-01-03 06:07:00\n"),
        ];
        for (name, body) in files {
            std::fs::write(dir.join(format!("{name}.csv")), body).unwrap();
        }
    }

    #[test]
    fn timestamps_parse_to_epoch_microseconds() {
        assert_eq!(parse_timestamp("1970-01-01 00:00:00").unwrap(), 0);
        assert_eq!(parse_timestamp("1970-01-02").unwrap(), 86_400_000_000);
        assert_eq!(
            parse_timestamp("2024-01-03T05:07:00").unwrap(),
            1_704_258_420_000_000
        );
        assert_eq!(
            parse_timestamp("2000-03-01 00:00:00.5").unwrap()
                - parse_timestamp("2000-02-29 00:00:00").unwrap(),
            86_400_500_000
        );
        assert_eq!(parse_timestamp("1969-12-31 23:59:59").unwrap(), -1_000_000);
        for bad in [
            "",
            "2024-13-01",
            "2024-01-01 25:00:00",
            "yesterday",
            "2024-01",
        ] {
            assert!(parse_timestamp(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn csv_lines_handle_quotes_and_reject_unterminated_ones() {
        assert_eq!(
            split_csv_line("1,\"a, b\",\"say \"\"hi\"\"\",").unwrap(),
            vec!["1", "a, b", "say \"hi\"", ""]
        );
        assert!(split_csv_line("1,\"oops").is_err());
    }

    #[test]
    fn loads_every_table_builds_indexes_and_survives_restart() {
        let csv = temp_dir("loader-csv");
        let out = temp_dir("loader-out");
        write_sample_csvs(&csv);
        let (cat, report) = load_safehealth(&csv, &out.join("db")).unwrap();
        let counts: Vec<u64> = report.rows.iter().map(|(_, n)| *n).collect();
        assert_eq!(counts, vec![2, 1, 2, 1, 3, 4, 2, 1]);
        drop(cat);

        // Everything below goes through the on-disk catalog, i.e. after a "restart".
        let cat = Catalog::open(&out.join("db")).unwrap();
        let visits = cat.table("visits").unwrap();
        assert!(visits.is_fact);
        assert_eq!(visits.row_estimate, 4);
        assert_eq!(
            visits.privacy,
            Some(TablePrivacy {
                max_rows_per_unit: 5,
                max_groups_per_unit: 3
            })
        );
        assert_eq!(
            visits.column_privacy["total_charge"].clamp,
            Some((0.0, 1000.0))
        );
        assert_eq!(
            cat.table("patients").unwrap().access("mrn"),
            AccessMode::Denied
        );

        let heap = cat.open_heap("visits").unwrap();
        assert_eq!(heap.row_count().unwrap(), 4);
        let types = visits.column_types();
        let rows: Vec<_> = heap
            .scan()
            .unwrap()
            .into_iter()
            .map(|(_, b)| decode_row(&types, &b).unwrap())
            .collect();
        assert_eq!(rows[0][0], Value::I64(1));
        assert_eq!(rows[0][4], Value::Timestamp(1_704_258_420_000_000));
        assert_eq!(rows[0][6], Value::F64(1200.5));
        assert_eq!(rows[0][7], Value::Text("EMERGENCY".into()));
        assert_eq!(rows[3][6], Value::F64(10.0));

        // Point lookup by primary key, then the row it points at.
        let pk = cat.open_index("visits", "visit_id").unwrap();
        let rid = pk.lookup(3).unwrap();
        assert_eq!(rid.len(), 1);
        let row = decode_row(&types, &heap.get(rid[0]).unwrap().unwrap()).unwrap();
        assert_eq!(row[0], Value::I64(3));
        assert_eq!(row[4], Value::Timestamp(86_400_000_000));

        // Non-unique index on the privacy unit; range scan over keys.
        let by_patient = cat.open_index("visits", "patient_id").unwrap();
        assert_eq!(by_patient.lookup(1).unwrap().len(), 2);
        assert_eq!(by_patient.range(Some(2), Some(3)).unwrap().len(), 2);
        assert_eq!(pk.range(None, None).unwrap().len(), 4);
        by_patient.check().unwrap();

        assert!(cat.open_index("visits", "dept_id").is_err());
        assert!(cat.open_heap("nope").is_err());
        std::fs::remove_dir_all(csv).unwrap();
        std::fs::remove_dir_all(out).unwrap();
    }

    #[test]
    fn a_bad_value_names_the_file_and_line() {
        let csv = temp_dir("loader-bad-csv");
        let out = temp_dir("loader-bad-out");
        write_sample_csvs(&csv);
        std::fs::write(
            csv.join("regions.csv"),
            "region_id,region_name,state_code\n1,North,TN\nx,South,KL\n",
        )
        .unwrap();
        let err = load_safehealth(&csv, &out.join("db"))
            .unwrap_err()
            .to_string();
        assert!(err.contains("regions.csv line 3"), "{err}");
        std::fs::remove_dir_all(csv).unwrap();
        std::fs::remove_dir_all(out).unwrap();
    }

    #[test]
    fn a_missing_column_is_reported() {
        let csv = temp_dir("loader-missing-csv");
        let out = temp_dir("loader-missing-out");
        write_sample_csvs(&csv);
        std::fs::write(csv.join("regions.csv"), "region_id,region_name\n1,North\n").unwrap();
        let err = load_safehealth(&csv, &out.join("db"))
            .unwrap_err()
            .to_string();
        assert!(err.contains("missing column state_code"), "{err}");
        std::fs::remove_dir_all(csv).unwrap();
        std::fs::remove_dir_all(out).unwrap();
    }
}
