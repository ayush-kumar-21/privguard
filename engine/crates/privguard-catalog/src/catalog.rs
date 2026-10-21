//! The catalog proper: table/column definitions and privacy policy, persisted in the
//! engine's own heap files. `SPEC.md` §7 (`IMPLEMENTATION_PLAN.md` 2.5).
//!
//! The in-memory [`Catalog`] is loaded from, and saved to, one `sys_*.heap` file per bootstrap
//! table in the catalog directory. User tables keep their own `<table>.heap` files beside them.

use crate::schema::{sys_schema, types_of};
use crate::{AccessMode, BOOTSTRAP_TABLES};
use privguard_core::{ColumnType, Error, Result, Value};
use privguard_crypto::{Keyring, TableCipher};
use privguard_storage::{decode_row, encode_row, FilePageStore, HeapFile};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// One column of a user table (`sys_column`).
#[derive(Debug, Clone, PartialEq)]
pub struct ColumnDef {
    pub name: String,
    pub col_type: ColumnType,
    pub nullable: bool,
    pub encrypted: bool,
    pub filterable: bool,
    pub groupable: bool,
}

impl ColumnDef {
    pub fn new(name: &str, col_type: ColumnType) -> Self {
        ColumnDef {
            name: name.to_string(),
            col_type,
            nullable: false,
            encrypted: false,
            filterable: false,
            groupable: false,
        }
    }

    pub fn nullable(mut self) -> Self {
        self.nullable = true;
        self
    }

    pub fn filterable(mut self) -> Self {
        self.filterable = true;
        self
    }

    pub fn groupable(mut self) -> Self {
        self.groupable = true;
        self
    }
}

/// Per-column privacy policy (`sys_column_privacy`). A column with no entry is `Denied`.
#[derive(Debug, Clone, PartialEq)]
pub struct ColumnPrivacy {
    pub access: AccessMode,
    /// Mechanism name as spelled in `SPEC.md` §4.4; set when the column can be aggregated.
    pub mechanism: Option<String>,
    pub clamp: Option<(f64, f64)>,
}

/// Contribution bounds (`sys_table_privacy`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TablePrivacy {
    pub max_rows_per_unit: u32,
    pub max_groups_per_unit: u32,
}

/// A user table with its columns and policies.
#[derive(Debug, Clone, PartialEq)]
pub struct TableDef {
    pub table_id: i64,
    pub name: String,
    /// The privacy-unit fact table (`visits`); the only one analysts may aggregate over.
    pub is_fact: bool,
    pub row_estimate: i64,
    pub columns: Vec<ColumnDef>,
    pub privacy: Option<TablePrivacy>,
    pub column_privacy: BTreeMap<String, ColumnPrivacy>,
}

impl TableDef {
    pub fn new(name: &str, columns: Vec<ColumnDef>) -> Self {
        TableDef {
            table_id: 0,
            name: name.to_string(),
            is_fact: false,
            row_estimate: 0,
            columns,
            privacy: None,
            column_privacy: BTreeMap::new(),
        }
    }

    pub fn column(&self, name: &str) -> Option<(usize, &ColumnDef)> {
        self.columns
            .iter()
            .enumerate()
            .find(|(_, c)| c.name == name)
    }

    pub fn column_types(&self) -> Vec<ColumnType> {
        self.columns.iter().map(|c| c.col_type).collect()
    }

    /// Access mode of `column`; fails closed to `Denied` when no policy row exists.
    pub fn access(&self, column: &str) -> AccessMode {
        self.column_privacy
            .get(column)
            .map_or(AccessMode::Denied, |p| p.access)
    }
}

/// The whole catalog, held in memory and persisted to `dir`.
#[derive(Debug)]
pub struct Catalog {
    dir: PathBuf,
    pub database: String,
    pub default_delta: f64,
    tables: BTreeMap<String, TableDef>,
    next_table_id: i64,
    keyring: Option<Keyring>,
}

fn parse_type(s: &str) -> Result<ColumnType> {
    Ok(match s {
        "i32" => ColumnType::I32,
        "i64" => ColumnType::I64,
        "f64" => ColumnType::F64,
        "text" => ColumnType::Text,
        "timestamp" => ColumnType::Timestamp,
        "bool" => ColumnType::Bool,
        other => return Err(Error::catalog(format!("unknown column type: {other}"))),
    })
}

fn access_name(a: AccessMode) -> &'static str {
    match a {
        AccessMode::Allowed => "allowed",
        AccessMode::AggregateOnly => "aggregate_only",
        AccessMode::Denied => "denied",
    }
}

fn parse_access(s: &str) -> Result<AccessMode> {
    Ok(match s {
        "allowed" => AccessMode::Allowed,
        "aggregate_only" => AccessMode::AggregateOnly,
        "denied" => AccessMode::Denied,
        other => return Err(Error::catalog(format!("unknown access mode: {other}"))),
    })
}

fn text(v: &Value) -> Result<String> {
    match v {
        Value::Text(s) => Ok(s.clone()),
        other => Err(Error::catalog(format!("expected text, got {other:?}"))),
    }
}

fn int(v: &Value) -> Result<i64> {
    match v {
        Value::I64(x) => Ok(*x),
        Value::I32(x) => Ok(i64::from(*x)),
        other => Err(Error::catalog(format!("expected integer, got {other:?}"))),
    }
}

fn flag(v: &Value) -> Result<bool> {
    match v {
        Value::Bool(x) => Ok(*x),
        other => Err(Error::catalog(format!("expected bool, got {other:?}"))),
    }
}

fn float(v: &Value) -> Result<f64> {
    match v {
        Value::F64(x) => Ok(*x),
        other => Err(Error::catalog(format!("expected float, got {other:?}"))),
    }
}

/// The table a `sys_*` row's `table_id` column points at.
fn owner<'a>(
    tables: &'a mut BTreeMap<String, TableDef>,
    by_id: &BTreeMap<i64, String>,
    id: &Value,
) -> Result<&'a mut TableDef> {
    let id = int(id)?;
    let name = by_id
        .get(&id)
        .ok_or_else(|| Error::catalog(format!("dangling table_id {id}")))?;
    Ok(tables.get_mut(name).expect("indexed with by_id"))
}

/// Read every row of the bootstrap table `name` from `dir`.
pub fn read_sys_rows(dir: &Path, name: &str) -> Result<Vec<Vec<Value>>> {
    let path = dir.join(format!("{name}.heap"));
    if !path.exists() {
        return Err(Error::catalog(format!("{} not found", path.display())));
    }
    let types = types_of(sys_schema(name).expect("bootstrap table"));
    let heap = HeapFile::open(FilePageStore::open(&path)?)?;
    heap.scan()?
        .into_iter()
        .map(|(_, bytes)| decode_row(&types, &bytes))
        .collect()
}

/// Replace the contents of bootstrap table `name` in `dir` with `rows`.
///
/// Atomic: the new contents are written to `<name>.heap.tmp`, fsynced, then renamed over the old
/// file, so a crash or a failed row leaves the previous table intact. Rewriting in place would
/// leave a truncated ledger after a crash, and a prefix of a hash chain still verifies, which
/// would silently refund spent budget and empty the noise cache.
// ponytail: no directory fsync after the rename, so a power cut right after can still roll back to
// the previous (complete) table. Add `File::open(dir)?.sync_all()` on unix if that matters.
pub fn write_sys_rows(dir: &Path, name: &str, rows: &[Vec<Value>]) -> Result<()> {
    let schema = sys_schema(name)
        .ok_or_else(|| Error::catalog(format!("{name} is not a bootstrap table")))?;
    let types = types_of(schema);
    let path = dir.join(format!("{name}.heap"));
    let tmp = dir.join(format!("{name}.heap.tmp"));
    let io =
        |what: &str, e: std::io::Error| Error::engine(format!("{what} {}: {e}", tmp.display()));

    let written = (|| -> Result<()> {
        let mut heap = HeapFile::open(FilePageStore::create(&tmp)?)?;
        for row in rows {
            heap.insert(&encode_row(&types, row)?)?;
        }
        Ok(())
    })();
    if let Err(e) = written {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    std::fs::File::open(&tmp)
        .and_then(|f| f.sync_all())
        .map_err(|e| io("cannot sync", e))?;
    std::fs::rename(&tmp, &path).map_err(|e| io("cannot rename", e))
}

impl Catalog {
    /// Create an empty catalog in `dir` (created if missing) and persist it.
    pub fn create(dir: &Path, database: &str, default_delta: f64) -> Result<Self> {
        std::fs::create_dir_all(dir)
            .map_err(|e| Error::engine(format!("cannot create {}: {e}", dir.display())))?;
        let cat = Catalog {
            dir: dir.to_path_buf(),
            database: database.to_string(),
            default_delta,
            tables: BTreeMap::new(),
            next_table_id: 1,
            keyring: None,
        };
        cat.save()?;
        Ok(cat)
    }

    /// Load the catalog previously saved in `dir`.
    pub fn open(dir: &Path) -> Result<Self> {
        let db = read_sys_rows(dir, "sys_database")?;
        let db = db
            .first()
            .ok_or_else(|| Error::catalog("sys_database is empty"))?;
        let mut cat = Catalog {
            dir: dir.to_path_buf(),
            database: text(&db[0])?,
            default_delta: float(&db[1])?,
            tables: BTreeMap::new(),
            next_table_id: 1,
            keyring: None,
        };

        let mut by_id: BTreeMap<i64, String> = BTreeMap::new();
        for r in read_sys_rows(dir, "sys_table")? {
            let def = TableDef {
                table_id: int(&r[0])?,
                name: text(&r[1])?,
                is_fact: flag(&r[2])?,
                row_estimate: int(&r[3])?,
                columns: Vec::new(),
                privacy: None,
                column_privacy: BTreeMap::new(),
            };
            cat.next_table_id = cat.next_table_id.max(def.table_id + 1);
            by_id.insert(def.table_id, def.name.clone());
            cat.tables.insert(def.name.clone(), def);
        }

        let mut cols = read_sys_rows(dir, "sys_column")?;
        cols.sort_by_key(|r| (int(&r[0]).unwrap_or(0), int(&r[1]).unwrap_or(0)));
        for r in cols {
            owner(&mut cat.tables, &by_id, &r[0])?
                .columns
                .push(ColumnDef {
                    name: text(&r[2])?,
                    col_type: parse_type(&text(&r[3])?)?,
                    nullable: flag(&r[4])?,
                    encrypted: flag(&r[5])?,
                    filterable: flag(&r[6])?,
                    groupable: flag(&r[7])?,
                });
        }
        for r in read_sys_rows(dir, "sys_table_privacy")? {
            owner(&mut cat.tables, &by_id, &r[0])?.privacy = Some(TablePrivacy {
                max_rows_per_unit: int(&r[1])? as u32,
                max_groups_per_unit: int(&r[2])? as u32,
            });
        }
        for r in read_sys_rows(dir, "sys_column_privacy")? {
            let clamp = match (&r[3], &r[4]) {
                (Value::F64(lo), Value::F64(hi)) => Some((*lo, *hi)),
                _ => None,
            };
            let mechanism = match &r[2] {
                Value::Text(s) => Some(s.clone()),
                _ => None,
            };
            let privacy = ColumnPrivacy {
                access: parse_access(&text(&r[5])?)?,
                mechanism,
                clamp,
            };
            owner(&mut cat.tables, &by_id, &r[0])?
                .column_privacy
                .insert(text(&r[1])?, privacy);
        }
        Ok(cat)
    }

    /// Provide the column-encryption key (`SPEC.md` §5.5). It lives in the process, never in the
    /// catalog files.
    pub fn set_keyring(&mut self, keyring: Option<Keyring>) {
        self.keyring = keyring;
    }

    /// The cipher for `table`'s encrypted columns; `None` when it has none. Errors rather than
    /// returning garbage if columns are encrypted and no key was provided.
    pub fn table_cipher(&self, table: &str) -> Result<Option<TableCipher>> {
        let def = self
            .table(table)
            .ok_or_else(|| Error::catalog(format!("unknown table: {table}")))?;
        if !def.columns.iter().any(|c| c.encrypted) {
            return Ok(None);
        }
        let key = self.keyring.as_ref().ok_or_else(|| {
            Error::policy(format!(
                "{table} has encrypted columns and no key is loaded (set PRIVGUARD_COLUMN_KEY)"
            ))
        })?;
        let columns: Vec<Option<String>> = def
            .columns
            .iter()
            .map(|c| c.encrypted.then(|| c.name.clone()))
            .collect();
        Ok(Some(key.table_cipher(table, &columns)))
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Path of the heap file that stores user table `table`.
    pub fn heap_path(&self, table: &str) -> PathBuf {
        self.dir.join(format!("{table}.heap"))
    }

    /// Path of the B+ tree index file on `table.column`.
    pub fn index_path(&self, table: &str, column: &str) -> PathBuf {
        self.dir.join(format!("{table}.{column}.idx"))
    }

    pub fn table(&self, name: &str) -> Option<&TableDef> {
        self.tables.get(name)
    }

    pub fn tables(&self) -> impl Iterator<Item = &TableDef> {
        self.tables.values()
    }

    /// Register a new table and assign its id.
    pub fn add_table(&mut self, mut def: TableDef) -> Result<i64> {
        if self.tables.contains_key(&def.name) {
            return Err(Error::catalog(format!("table {} already exists", def.name)));
        }
        if BOOTSTRAP_TABLES.contains(&def.name.as_str()) {
            return Err(Error::catalog(format!("{} is a reserved name", def.name)));
        }
        def.table_id = self.next_table_id;
        self.next_table_id += 1;
        let id = def.table_id;
        self.tables.insert(def.name.clone(), def);
        Ok(id)
    }

    fn table_mut(&mut self, name: &str) -> Result<&mut TableDef> {
        self.tables
            .get_mut(name)
            .ok_or_else(|| Error::catalog(format!("unknown table: {name}")))
    }

    pub fn set_table_privacy(&mut self, table: &str, c: u32, g: u32) -> Result<()> {
        if c < 1 || g < 1 {
            return Err(Error::policy("contribution bounds must be at least 1"));
        }
        self.table_mut(table)?.privacy = Some(TablePrivacy {
            max_rows_per_unit: c,
            max_groups_per_unit: g,
        });
        Ok(())
    }

    pub fn set_column_privacy(
        &mut self,
        table: &str,
        column: &str,
        privacy: ColumnPrivacy,
    ) -> Result<()> {
        let t = self.table_mut(table)?;
        if t.column(column).is_none() {
            return Err(Error::catalog(format!("unknown column: {table}.{column}")));
        }
        if let Some((lo, hi)) = privacy.clamp {
            if !(lo.is_finite() && hi.is_finite() && lo <= hi) || lo.abs().max(hi.abs()) == 0.0 {
                return Err(Error::policy(format!("invalid clamp bounds [{lo}, {hi}]")));
            }
        }
        t.column_privacy.insert(column.to_string(), privacy);
        Ok(())
    }

    pub fn set_row_estimate(&mut self, table: &str, rows: i64) -> Result<()> {
        self.table_mut(table)?.row_estimate = rows;
        Ok(())
    }

    /// Rewrite the catalog's own `sys_*` heap files from the in-memory state.
    ///
    /// The catalog is tiny and written rarely, so a full rewrite is simpler than in-place
    /// update. Bootstrap tables this type does not own (RBAC, ledger, caches) are created empty
    /// if missing and otherwise left alone.
    pub fn save(&self) -> Result<()> {
        let mut rows: BTreeMap<&str, Vec<Vec<Value>>> = BTreeMap::new();
        rows.insert(
            "sys_database",
            vec![vec![
                Value::Text(self.database.clone()),
                Value::F64(self.default_delta),
            ]],
        );
        for t in self.tables.values() {
            rows.entry("sys_table").or_default().push(vec![
                Value::I64(t.table_id),
                Value::Text(t.name.clone()),
                Value::Bool(t.is_fact),
                Value::I64(t.row_estimate),
            ]);
            for (i, c) in t.columns.iter().enumerate() {
                rows.entry("sys_column").or_default().push(vec![
                    Value::I64(t.table_id),
                    Value::I32(i as i32),
                    Value::Text(c.name.clone()),
                    Value::Text(c.col_type.to_string()),
                    Value::Bool(c.nullable),
                    Value::Bool(c.encrypted),
                    Value::Bool(c.filterable),
                    Value::Bool(c.groupable),
                ]);
            }
            if let Some(p) = t.privacy {
                rows.entry("sys_table_privacy").or_default().push(vec![
                    Value::I64(t.table_id),
                    Value::I32(p.max_rows_per_unit as i32),
                    Value::I32(p.max_groups_per_unit as i32),
                ]);
            }
            for (col, p) in &t.column_privacy {
                let (lo, hi) = p.clamp.map_or((Value::Null, Value::Null), |(lo, hi)| {
                    (Value::F64(lo), Value::F64(hi))
                });
                rows.entry("sys_column_privacy").or_default().push(vec![
                    Value::I64(t.table_id),
                    Value::Text(col.clone()),
                    p.mechanism.clone().map_or(Value::Null, Value::Text),
                    lo,
                    hi,
                    Value::Text(access_name(p.access).to_string()),
                ]);
            }
        }

        for name in BOOTSTRAP_TABLES {
            let owned = matches!(
                *name,
                "sys_database"
                    | "sys_table"
                    | "sys_column"
                    | "sys_table_privacy"
                    | "sys_column_privacy"
            );
            let path = self.dir.join(format!("{name}.heap"));
            if !owned && path.exists() {
                continue;
            }
            let types = types_of(sys_schema(name).expect("bootstrap table"));
            let mut heap = HeapFile::open(FilePageStore::create(&path)?)?;
            for row in rows.get(name).into_iter().flatten() {
                heap.insert(&encode_row(&types, row)?)?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    fn temp_dir(tag: &str) -> PathBuf {
        static N: AtomicU32 = AtomicU32::new(0);
        let d = std::env::temp_dir().join(format!(
            "privguard-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&d);
        d
    }

    fn visits() -> TableDef {
        let mut t = TableDef::new(
            "visits",
            vec![
                ColumnDef::new("visit_id", ColumnType::I64),
                ColumnDef::new("patient_id", ColumnType::I64),
                ColumnDef::new("dept_id", ColumnType::I64)
                    .filterable()
                    .groupable(),
                ColumnDef::new("total_charge", ColumnType::F64)
                    .nullable()
                    .filterable(),
                ColumnDef::new("admission_type", ColumnType::Text)
                    .filterable()
                    .groupable(),
            ],
        );
        t.is_fact = true;
        t
    }

    fn policy(access: AccessMode, clamp: Option<(f64, f64)>) -> ColumnPrivacy {
        ColumnPrivacy {
            access,
            mechanism: clamp.map(|_| "laplace".to_string()),
            clamp,
        }
    }

    #[test]
    fn a_saved_catalog_survives_a_restart() {
        let dir = temp_dir("catalog");
        let mut cat = Catalog::create(&dir, "safehealth", 1e-6).unwrap();
        assert_eq!(cat.add_table(visits()).unwrap(), 1);
        cat.set_table_privacy("visits", 5, 3).unwrap();
        cat.set_column_privacy(
            "visits",
            "total_charge",
            policy(AccessMode::AggregateOnly, Some((0.0, 1000.0))),
        )
        .unwrap();
        cat.set_column_privacy("visits", "patient_id", policy(AccessMode::Denied, None))
            .unwrap();
        cat.set_row_estimate("visits", 250).unwrap();
        cat.save().unwrap();
        drop(cat);

        let cat = Catalog::open(&dir).unwrap();
        assert_eq!(cat.database, "safehealth");
        assert_eq!(cat.default_delta, 1e-6);
        let t = cat.table("visits").unwrap();
        assert_eq!(t.table_id, 1);
        assert!(t.is_fact);
        assert_eq!(t.row_estimate, 250);
        assert_eq!(t.columns, visits().columns);
        assert_eq!(
            t.privacy,
            Some(TablePrivacy {
                max_rows_per_unit: 5,
                max_groups_per_unit: 3
            })
        );
        let p = &t.column_privacy["total_charge"];
        assert_eq!(p.clamp, Some((0.0, 1000.0)));
        assert_eq!(p.mechanism.as_deref(), Some("laplace"));
        assert_eq!(t.access("patient_id"), AccessMode::Denied);
        // No policy row: fail closed.
        assert_eq!(t.access("dept_id"), AccessMode::Denied);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn every_bootstrap_table_exists_on_disk() {
        let dir = temp_dir("catalog-boot");
        Catalog::create(&dir, "db", 1e-6).unwrap();
        for t in BOOTSTRAP_TABLES {
            assert!(dir.join(format!("{t}.heap")).exists(), "{t}");
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn table_ids_continue_after_a_restart() {
        let dir = temp_dir("catalog-ids");
        let mut cat = Catalog::create(&dir, "db", 1e-6).unwrap();
        cat.add_table(visits()).unwrap();
        cat.save().unwrap();
        let mut cat = Catalog::open(&dir).unwrap();
        let id = cat.add_table(TableDef::new("regions", vec![])).unwrap();
        assert_eq!(id, 2);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn bad_definitions_are_rejected() {
        let dir = temp_dir("catalog-bad");
        let mut cat = Catalog::create(&dir, "db", 1e-6).unwrap();
        cat.add_table(visits()).unwrap();
        assert!(cat.add_table(visits()).is_err());
        assert!(cat.add_table(TableDef::new("sys_table", vec![])).is_err());
        assert!(cat.set_table_privacy("visits", 0, 3).is_err());
        assert!(cat.set_table_privacy("nope", 1, 1).is_err());
        let agg = |clamp| policy(AccessMode::AggregateOnly, clamp);
        assert!(cat.set_column_privacy("visits", "nope", agg(None)).is_err());
        let bad = [(5.0, 1.0), (0.0, 0.0), (0.0, f64::INFINITY)];
        for b in bad {
            assert!(
                cat.set_column_privacy("visits", "total_charge", agg(Some(b)))
                    .is_err(),
                "{b:?}"
            );
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn opening_a_directory_with_no_catalog_is_a_catalog_error() {
        let dir = temp_dir("catalog-none");
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(Catalog::open(&dir).unwrap_err().sqlstate().code(), "42P01");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
