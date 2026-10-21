//! The engine facade: one object that turns `(principal, SQL, rho)` into a private answer.
//!
//! The order of steps is the security argument, and the comments below are the proof sketch:
//!
//! ```text
//! bind -> RBAC -> fingerprint -> cache? --yes--> replay stored answer, charge 0
//!                                   |no
//!         reserve budget  (no data page has been read yet)
//!         overlap check -> build plan -> run -> commit    (any failure here => abort)
//!         store answer in the noise cache -> audit -> persist
//! ```
//!
//! Single writer: callers wrap the engine in a mutex (`SPEC.md` §1 has no concurrency control).

use privguard_catalog::{read_sys_rows, write_sys_rows, Catalog};
use privguard_core::json::Json;
use privguard_core::{ColumnType, Error, Result, Row, Value};
use privguard_plan::build::{
    build_dp, calibrate, catalog_lookup, dp_operators, matching_row_ids, PRIVACY_UNIT,
};
use privguard_plan::logical::plan as logical_plan;
use privguard_plan::rbac::{self, Grants};
use privguard_plan::rewriter::{
    filter_key, fingerprint, CachedAnswer, NoiseCache, OverlapDetector,
};
use privguard_plan::PhysicalOp;
use privguard_privacy::accounting::eps_from_rho;
use privguard_privacy::ledger::{Balance, BudgetLedger, ChargeMeta, LedgerEntry};
use privguard_privacy::{defaults, derive_seed};
use privguard_sql::{parse_and_bind, Agg, BoundQuery};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;

/// Largest rho a single query may ask for.
pub const MAX_RHO: f64 = 5.0;

#[derive(Debug, Clone, PartialEq)]
pub struct Principal {
    pub id: u64,
    pub name: String,
    pub roles: Vec<String>,
}

impl Principal {
    /// Roles that may read every principal's budget and audit rows.
    pub fn is_privileged(&self) -> bool {
        self.roles.iter().any(|r| r == "dba" || r == "auditor")
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct AuditRecord {
    pub query_id: u64,
    pub principal_id: u64,
    pub fingerprint: String,
    /// `executed`, `cache_hit`, `rejected_budget` or `rejected_policy`.
    pub status: String,
    pub rho: f64,
    pub ts_micros: i64,
    pub sql: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct QueryOutput {
    pub status: &'static str,
    pub columns: Vec<(String, ColumnType)>,
    pub rows: Vec<Row>,
    pub rho_charged: f64,
    pub epsilon_charged: f64,
    pub mechanism: String,
    pub sensitivity: f64,
    pub scale: f64,
    pub query_id: u64,
    pub suppressed_groups: u64,
    pub budget: Balance,
}

/// One row of the policy grid: a column of the fact table and how analysts may use it.
#[derive(Debug, Clone, PartialEq)]
pub struct PolicyColumn {
    pub table: String,
    pub column: String,
    pub col_type: ColumnType,
    pub filterable: bool,
    pub groupable: bool,
    pub access: privguard_catalog::AccessMode,
    pub mechanism: Option<String>,
    pub clamp: Option<(f64, f64)>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LedgerVerdict {
    pub ok: bool,
    pub entries: usize,
    pub first_bad_entry: Option<usize>,
}

pub struct Engine {
    dir: PathBuf,
    catalog: Catalog,
    server_secret: Vec<u8>,
    principals: Vec<Principal>,
    role_ids: BTreeMap<String, u64>,
    grants: Grants,
    /// principal id -> budget account id
    accounts: BTreeMap<u64, u64>,
    ledger: BudgetLedger,
    cache: NoiseCache,
    overlap: OverlapDetector,
    audit: Vec<AuditRecord>,
    next_query: u64,
}

fn now_micros() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_micros() as i64)
}

fn int(v: &Value) -> Result<u64> {
    match v {
        Value::I64(x) => Ok(*x as u64),
        Value::I32(x) => Ok(*x as u64),
        other => Err(Error::catalog(format!("expected integer, got {other:?}"))),
    }
}

fn text(v: &Value) -> Result<String> {
    match v {
        Value::Text(s) => Ok(s.clone()),
        other => Err(Error::catalog(format!("expected text, got {other:?}"))),
    }
}

fn float(v: &Value) -> Result<f64> {
    match v {
        Value::F64(x) => Ok(*x),
        other => Err(Error::catalog(format!("expected float, got {other:?}"))),
    }
}

impl Engine {
    /// Open the database in `dir` (created by the loader). `server_secret` keys the noise.
    pub fn open(dir: &Path, server_secret: &[u8]) -> Result<Self> {
        if server_secret.len() < 16 {
            return Err(Error::policy("the server secret must be at least 16 bytes"));
        }
        let mut catalog = Catalog::open(dir)?;
        // The column key lives in the process environment, never in the catalog files.
        catalog.set_keyring(privguard_crypto::Keyring::from_env()?);
        let mut e = Engine {
            dir: dir.to_path_buf(),
            catalog,
            server_secret: server_secret.to_vec(),
            principals: Vec::new(),
            role_ids: BTreeMap::new(),
            grants: Grants::new(),
            accounts: BTreeMap::new(),
            ledger: BudgetLedger::new(),
            cache: NoiseCache::new(),
            overlap: OverlapDetector::new(),
            audit: Vec::new(),
            next_query: 1,
        };
        e.load()?;
        Ok(e)
    }

    fn load(&mut self) -> Result<()> {
        let dir = self.dir.clone();
        for r in read_sys_rows(&dir, "sys_role")? {
            self.role_ids.insert(text(&r[1])?, int(&r[0])?);
        }
        let role_name = |id: u64| {
            self.role_ids
                .iter()
                .find(|(_, v)| **v == id)
                .map(|(k, _)| k.clone())
                .ok_or_else(|| Error::catalog(format!("dangling role id {id}")))
        };
        let mut principals: BTreeMap<u64, Principal> = BTreeMap::new();
        for r in read_sys_rows(&dir, "sys_principal")? {
            let id = int(&r[0])?;
            principals.insert(
                id,
                Principal {
                    id,
                    name: text(&r[1])?,
                    roles: Vec::new(),
                },
            );
        }
        for r in read_sys_rows(&dir, "sys_principal_role")? {
            let (pid, rid) = (int(&r[0])?, int(&r[1])?);
            let role = role_name(rid)?;
            principals
                .get_mut(&pid)
                .ok_or_else(|| Error::catalog(format!("dangling principal id {pid}")))?
                .roles
                .push(role);
        }
        for r in read_sys_rows(&dir, "sys_column_grant")? {
            let (rid, tid, col) = (int(&r[0])?, int(&r[1])?, text(&r[2])?);
            let table = self
                .catalog
                .tables()
                .find(|t| t.table_id as u64 == tid)
                .map(|t| t.name.clone())
                .ok_or_else(|| Error::catalog(format!("dangling table id {tid}")))?;
            self.grants.grant(&role_name(rid)?, &table, &col);
        }
        self.principals = principals.into_values().collect();

        let mut totals = Vec::new();
        for r in read_sys_rows(&dir, "sys_budget_account")? {
            let (account, principal, total) = (int(&r[0])?, int(&r[1])?, float(&r[2])?);
            self.accounts.insert(principal, account);
            totals.push((account, total));
        }
        let entries = read_sys_rows(&dir, "sys_budget_ledger")?
            .iter()
            .map(LedgerEntry::from_row)
            .collect::<Result<Vec<_>>>()?;
        // `restore` replays the protocol and re-verifies every hash: a tampered ledger on disk
        // stops the engine from starting rather than from being quietly trusted.
        self.ledger = BudgetLedger::restore(&totals, entries)?;
        // A crash between reserve and settle leaves a dangling hold; release it.
        let mut released = 0;
        for (account, _) in &totals {
            released += self.ledger.abort_dangling(*account)?;
        }

        self.cache = NoiseCache::from_rows(&read_sys_rows(&dir, "sys_noise_cache")?)?;
        for r in read_sys_rows(&dir, "sys_query")? {
            self.audit.push(AuditRecord {
                query_id: int(&r[0])?,
                principal_id: int(&r[1])?,
                fingerprint: text(&r[2])?,
                status: text(&r[3])?,
                rho: float(&r[4])?,
                ts_micros: match r[5] {
                    Value::Timestamp(t) => t,
                    _ => 0,
                },
                sql: text(&r[6])?,
            });
        }
        self.next_query = self.audit.iter().map(|a| a.query_id).max().unwrap_or(0) + 1;
        if released > 0 {
            self.persist()?;
        }
        Ok(())
    }

    /// Write principals, grants, accounts, the ledger, the cache and the audit log to the
    /// `sys_*` heap files.
    pub fn persist(&self) -> Result<()> {
        let dir = &self.dir;
        let role_rows: Vec<Row> = self
            .role_ids
            .iter()
            .map(|(n, id)| vec![Value::I64(*id as i64), Value::Text(n.clone())])
            .collect();
        write_sys_rows(dir, "sys_role", &role_rows)?;

        let principal_rows: Vec<Row> = self
            .principals
            .iter()
            .map(|p| {
                vec![
                    Value::I64(p.id as i64),
                    Value::Text(p.name.clone()),
                    // Passwords are checked by the gateway; the engine trusts its service secret.
                    Value::Text("external".into()),
                ]
            })
            .collect();
        write_sys_rows(dir, "sys_principal", &principal_rows)?;

        let mut link_rows = Vec::new();
        for p in &self.principals {
            for role in &p.roles {
                link_rows.push(vec![
                    Value::I64(p.id as i64),
                    Value::I64(self.role_ids[role] as i64),
                ]);
            }
        }
        write_sys_rows(dir, "sys_principal_role", &link_rows)?;

        let mut grant_rows = Vec::new();
        for (role, table, column) in self.grants.iter() {
            let t = self
                .catalog
                .table(table)
                .ok_or_else(|| Error::catalog(format!("grant on unknown table {table}")))?;
            grant_rows.push(vec![
                Value::I64(self.role_ids[role] as i64),
                Value::I64(t.table_id),
                Value::Text(column.clone()),
            ]);
        }
        write_sys_rows(dir, "sys_column_grant", &grant_rows)?;

        let account_rows: Vec<Row> = self
            .ledger
            .accounts()
            .into_iter()
            .map(|(account, total)| {
                let principal = self
                    .accounts
                    .iter()
                    .find(|(_, a)| **a == account)
                    .map_or(0, |(p, _)| *p);
                vec![
                    Value::I64(account as i64),
                    Value::I64(principal as i64),
                    Value::F64(total),
                ]
            })
            .collect();
        write_sys_rows(dir, "sys_budget_account", &account_rows)?;

        let mut ledger_rows = Vec::new();
        for (account, _) in self.ledger.accounts() {
            ledger_rows.extend(
                self.ledger
                    .entries(account)?
                    .iter()
                    .map(LedgerEntry::to_row),
            );
        }
        write_sys_rows(dir, "sys_budget_ledger", &ledger_rows)?;

        write_sys_rows(dir, "sys_noise_cache", &self.cache.to_rows())?;

        let audit_rows: Vec<Row> = self
            .audit
            .iter()
            .map(|a| {
                vec![
                    Value::I64(a.query_id as i64),
                    Value::I64(a.principal_id as i64),
                    Value::Text(a.fingerprint.clone()),
                    Value::Text(a.status.clone()),
                    Value::F64(a.rho),
                    Value::Timestamp(a.ts_micros),
                    Value::Text(a.sql.clone()),
                ]
            })
            .collect();
        write_sys_rows(dir, "sys_query", &audit_rows)
    }

    // ------------------------------------------------------------- administration

    pub fn catalog(&self) -> &Catalog {
        &self.catalog
    }

    pub fn grants(&self) -> &Grants {
        &self.grants
    }

    pub fn principal(&self, name: &str) -> Option<&Principal> {
        self.principals.iter().find(|p| p.name == name)
    }

    fn principal_by_id(&self, id: u64) -> Option<&Principal> {
        self.principals.iter().find(|p| p.id == id)
    }

    /// Register a principal with `roles` and, if `rho_total` is given, a budget account.
    pub fn add_principal(
        &mut self,
        name: &str,
        roles: &[&str],
        rho_total: Option<f64>,
    ) -> Result<u64> {
        if self.principal(name).is_some() {
            return Err(Error::catalog(format!("principal {name} already exists")));
        }
        let id = self.principals.iter().map(|p| p.id).max().unwrap_or(0) + 1;
        for r in roles {
            let next = self.role_ids.len() as u64 + 1;
            self.role_ids.entry((*r).to_string()).or_insert(next);
        }
        self.principals.push(Principal {
            id,
            name: name.to_string(),
            roles: roles.iter().map(|r| (*r).to_string()).collect(),
        });
        if let Some(total) = rho_total {
            let account = self.accounts.values().max().copied().unwrap_or(0) + 1;
            self.ledger.open_account(account, total)?;
            self.accounts.insert(id, account);
        }
        self.persist()?;
        Ok(id)
    }

    /// Let `role` reference `table.column` in queries.
    pub fn grant(&mut self, role: &str, table: &str, column: &str) -> Result<()> {
        let t = self
            .catalog
            .table(table)
            .ok_or_else(|| Error::catalog(format!("unknown table: {table}")))?;
        if t.column(column).is_none() {
            return Err(Error::catalog(format!("unknown column: {table}.{column}")));
        }
        let next = self.role_ids.len() as u64 + 1;
        self.role_ids.entry(role.to_string()).or_insert(next);
        self.grants.grant(role, table, column);
        self.persist()
    }

    /// Withdraw a grant. Returns whether it existed.
    pub fn revoke(&mut self, role: &str, table: &str, column: &str) -> Result<bool> {
        let existed = self.grants.revoke(role, table, column);
        self.persist()?;
        Ok(existed)
    }

    // ------------------------------------------------------------- policy grid

    fn require_role(&self, actor: &str, roles: &[&str]) -> Result<()> {
        let p = self
            .principal(actor)
            .ok_or_else(|| Error::catalog(format!("unknown principal: {actor}")))?;
        if p.roles.iter().any(|r| roles.contains(&r.as_str())) {
            Ok(())
        } else {
            Err(Error::policy(format!(
                "requires one of the roles: {}",
                roles.join(", ")
            )))
        }
    }

    /// All role names, sorted.
    pub fn roles(&self) -> Vec<String> {
        self.role_ids.keys().cloned().collect()
    }

    /// Every column of the privacy-unit fact table with its policy, for the policy grid.
    /// Readable by `dba` and `auditor`.
    pub fn policy_columns(&self, actor: &str) -> Result<Vec<PolicyColumn>> {
        self.require_role(actor, &["dba", "auditor"])?;
        let mut out = Vec::new();
        for t in self.catalog.tables().filter(|t| t.is_fact) {
            for c in &t.columns {
                let p = t.column_privacy.get(&c.name);
                out.push(PolicyColumn {
                    table: t.name.clone(),
                    column: c.name.clone(),
                    col_type: c.col_type,
                    filterable: c.filterable,
                    groupable: c.groupable,
                    access: t.access(&c.name),
                    mechanism: p.and_then(|p| p.mechanism.clone()),
                    clamp: p.and_then(|p| p.clamp),
                });
            }
        }
        Ok(out)
    }

    /// `(role, table, column)` grants. Readable by `dba` and `auditor`.
    pub fn grant_rows(&self, actor: &str) -> Result<Vec<(String, String, String)>> {
        self.require_role(actor, &["dba", "auditor"])?;
        Ok(self.grants.iter().cloned().collect())
    }

    /// `GRANT` on behalf of `actor`, who must be a `dba`.
    pub fn grant_as(&mut self, actor: &str, role: &str, table: &str, column: &str) -> Result<()> {
        self.require_role(actor, &["dba"])?;
        if !self.role_ids.contains_key(role) {
            return Err(Error::catalog(format!("unknown role: {role}")));
        }
        self.grant(role, table, column)
    }

    /// `REVOKE` on behalf of `actor`, who must be a `dba`. Returns whether the grant existed.
    pub fn revoke_as(
        &mut self,
        actor: &str,
        role: &str,
        table: &str,
        column: &str,
    ) -> Result<bool> {
        self.require_role(actor, &["dba"])?;
        if !self.role_ids.contains_key(role) {
            return Err(Error::catalog(format!("unknown role: {role}")));
        }
        self.revoke(role, table, column)
    }

    // -------------------------------------------------------------------- queries

    fn bind(&self, sql: &str) -> Result<BoundQuery> {
        parse_and_bind(sql, &catalog_lookup(&self.catalog))
    }

    fn log(&mut self, p: &Principal, fp: &str, status: &str, rho: f64, sql: &str) -> u64 {
        let id = self.next_query;
        self.next_query += 1;
        self.audit.push(AuditRecord {
            query_id: id,
            principal_id: p.id,
            fingerprint: fp.to_string(),
            status: status.to_string(),
            rho,
            ts_micros: now_micros(),
            sql: sql.to_string(),
        });
        id
    }

    fn output_columns(&self, q: &BoundQuery) -> Result<Vec<(String, ColumnType)>> {
        // Only a scalar COUNT is an integer: grouped answers carry Gaussian noise, so they are
        // fractional even for COUNT.
        let value_type = if q.agg == Agg::Count && q.group_by.is_none() {
            ColumnType::I64
        } else {
            ColumnType::F64
        };
        let mut cols = Vec::new();
        if let Some(g) = &q.group_by {
            let table = self
                .catalog
                .table(&q.table)
                .ok_or_else(|| Error::catalog(format!("unknown table: {}", q.table)))?;
            let (_, def) = table
                .column(g)
                .ok_or_else(|| Error::catalog(format!("unknown column: {g}")))?;
            cols.push((g.clone(), def.col_type));
        }
        cols.push(("value".to_string(), value_type));
        Ok(cols)
    }

    /// Answer `sql` for `principal_name` at privacy cost `rho`.
    pub fn query(&mut self, principal_name: &str, sql: &str, rho: f64) -> Result<QueryOutput> {
        let principal = self
            .principal(principal_name)
            .cloned()
            .ok_or_else(|| Error::catalog(format!("unknown principal: {principal_name}")))?;
        if !(rho.is_finite() && rho > 0.0 && rho <= MAX_RHO) {
            return Err(Error::policy(format!("rho must be in (0, {MAX_RHO}]")));
        }

        let query = self.bind(sql)?;
        let fp = fingerprint(&query);
        let fp_hex = fp.to_hex();

        if let Err(e) = rbac::check(&query, &principal.roles, &self.grants) {
            self.log(&principal, &fp_hex, "rejected_policy", 0.0, sql);
            self.persist()?;
            return Err(e);
        }
        let account = match self.accounts.get(&principal.id) {
            Some(a) => *a,
            None => {
                self.log(&principal, &fp_hex, "rejected_policy", 0.0, sql);
                self.persist()?;
                return Err(Error::policy("this principal has no budget account"));
            }
        };
        let columns = self.output_columns(&query)?;

        // Cache first: the same question from the same principal gets the stored answer and
        // costs nothing, so repeating it cannot buy a second independent noise draw.
        if let Some(hit) = self.cache.get(&fp, principal.id).cloned() {
            let id = self.log(&principal, &fp_hex, "cache_hit", 0.0, sql);
            self.persist()?;
            return Ok(QueryOutput {
                status: "cache_hit",
                columns,
                rows: hit.rows,
                rho_charged: 0.0,
                epsilon_charged: 0.0,
                mechanism: hit.mechanism,
                sensitivity: hit.sensitivity,
                scale: hit.scale,
                query_id: id,
                suppressed_groups: hit.suppressed_groups,
                budget: self.ledger.balance(account)?,
            });
        }

        // Calibration reads no data, so it can be recorded in the reservation.
        let cal = calibrate(&self.catalog, &query, rho)?;
        let query_id = self.next_query;
        let meta = ChargeMeta {
            mechanism: cal.mechanism.to_string(),
            sensitivity: cal.sensitivity,
            scale: cal.scale,
            eps_equiv: cal.eps_equivalent,
        };
        // No data page is read before this succeeds (`SPEC.md` §6).
        if let Err(e) = self.ledger.reserve(account, query_id, rho, meta) {
            self.log(&principal, &fp_hex, "rejected_budget", 0.0, sql);
            self.persist()?;
            return Err(e);
        }

        let ran = self.run_reserved(&principal, &query, &fp, rho);
        let (rows, suppressed, cal) = match ran {
            Ok(done) => done,
            Err(e) => {
                // A failure after a successful reservation must settle it (`SPEC.md` §9).
                self.ledger.abort(account, query_id)?;
                let status = if matches!(e, Error::Policy(_)) {
                    "rejected_policy"
                } else {
                    "error"
                };
                self.log(&principal, &fp_hex, status, 0.0, sql);
                self.persist()?;
                return Err(e);
            }
        };
        self.ledger.commit(account, query_id)?;

        let answer = CachedAnswer {
            rows: rows.clone(),
            mechanism: cal.mechanism.to_string(),
            sensitivity: cal.sensitivity,
            scale: cal.scale,
            rho,
            epsilon: cal.eps_equivalent,
            suppressed_groups: suppressed,
        };
        self.cache.insert(&fp, principal.id, answer);
        let logged = self.log(&principal, &fp_hex, "executed", rho, sql);
        debug_assert_eq!(logged, query_id);
        self.persist()?;
        Ok(QueryOutput {
            status: "executed",
            columns,
            rows,
            rho_charged: rho,
            epsilon_charged: eps_from_rho(rho, self.catalog.default_delta),
            mechanism: cal.mechanism.to_string(),
            sensitivity: cal.sensitivity,
            scale: cal.scale,
            query_id,
            suppressed_groups: suppressed,
            budget: self.ledger.balance(account)?,
        })
    }

    /// Everything that touches data. Runs while a reservation is held.
    fn run_reserved(
        &mut self,
        principal: &Principal,
        query: &BoundQuery,
        fp: &privguard_privacy::QueryFingerprint,
        rho: f64,
    ) -> Result<(Vec<Row>, u64, privguard_privacy::operators::Calibration)> {
        // Overlap detection reads the matched rows, hence after the reservation.
        let matched = matching_row_ids(&self.catalog, query)?;
        let key = filter_key(query);
        self.overlap.check(principal.id, &key, &matched)?;

        let seed = derive_seed(&self.server_secret, fp.as_bytes(), principal.id, b"");
        let mut plan = build_dp(&self.catalog, query, rho, &seed)?;
        let rows = privguard_core::RowSource::collect_rows(&mut plan.source)?;
        let suppressed = plan
            .suppressed
            .as_ref()
            .map_or(0, |c| c.load(Ordering::Relaxed));
        // Only an answer that was actually released counts as "seen".
        self.overlap.record(principal.id, key, matched);
        Ok((rows, suppressed, plan.calibration))
    }

    /// The plan `sql` would run, as JSON for the plan viewer. Reads no data and spends nothing.
    pub fn explain(&self, principal_name: &str, sql: &str) -> Result<String> {
        let principal = self
            .principal(principal_name)
            .ok_or_else(|| Error::catalog(format!("unknown principal: {principal_name}")))?;
        let query = self.bind(sql)?;
        rbac::check(&query, &principal.roles, &self.grants)?;
        let fp = fingerprint(&query);
        let logical = logical_plan(&query);
        let calibration = calibrate(&self.catalog, &query, defaults::RHO)?;

        let physical: Vec<Json> = dp_operators(&query)
            .into_iter()
            .map(|op| Json::str(op_name(op)))
            .collect();
        let logical_ops: Vec<Json> = logical.operators().into_iter().map(Json::str).collect();
        let cached = self.cache.get(&fp, principal.id).is_some();
        Ok(Json::Obj(vec![
            ("fingerprint".into(), Json::str(fp.to_hex())),
            ("cached".into(), Json::Bool(cached)),
            ("logical".into(), Json::Arr(logical_ops)),
            ("logical_text".into(), Json::str(logical.explain())),
            ("physical".into(), Json::Arr(physical)),
            ("mechanism".into(), Json::str(calibration.mechanism)),
            ("sensitivity".into(), Json::float(calibration.sensitivity)),
            ("privacy_unit".into(), Json::str(PRIVACY_UNIT)),
        ])
        .to_json_string())
    }

    // ---------------------------------------------------------- budget and audit

    pub fn balance(&self, principal_name: &str) -> Result<Option<(u64, Balance)>> {
        let p = self
            .principal(principal_name)
            .ok_or_else(|| Error::catalog(format!("unknown principal: {principal_name}")))?;
        match self.accounts.get(&p.id) {
            Some(a) => Ok(Some((*a, self.ledger.balance(*a)?))),
            None => Ok(None),
        }
    }

    /// Budget rows the principal may see: their own, or everyone's if privileged.
    pub fn balances(&self, principal_name: &str) -> Result<Vec<(String, u64, Balance)>> {
        let me = self
            .principal(principal_name)
            .ok_or_else(|| Error::catalog(format!("unknown principal: {principal_name}")))?;
        let mut out = Vec::new();
        for (pid, account) in &self.accounts {
            if me.is_privileged() || *pid == me.id {
                let name = self
                    .principal_by_id(*pid)
                    .map_or_else(String::new, |p| p.name.clone());
                out.push((name, *account, self.ledger.balance(*account)?));
            }
        }
        Ok(out)
    }

    /// Re-verify the principal's hash chain from the first entry.
    pub fn verify_ledger(&self, principal_name: &str) -> Result<Option<LedgerVerdict>> {
        let Some((account, _)) = self.balance(principal_name)? else {
            return Ok(None);
        };
        let entries = self.ledger.entries(account)?.len();
        Ok(Some(match self.ledger.verify() {
            Ok(()) => LedgerVerdict {
                ok: true,
                entries,
                first_bad_entry: None,
            },
            Err(e) => {
                // "... entry N: ..." names the first bad entry.
                let first = e
                    .to_string()
                    .split("entry ")
                    .nth(1)
                    .and_then(|s| s.split(':').next())
                    .and_then(|s| s.trim().parse().ok());
                LedgerVerdict {
                    ok: false,
                    entries,
                    first_bad_entry: first,
                }
            }
        }))
    }

    /// Audit rows visible to `principal_name`, oldest first, with the author's name.
    pub fn audit_rows(&self, principal_name: &str) -> Result<Vec<(String, AuditRecord)>> {
        let me = self
            .principal(principal_name)
            .ok_or_else(|| Error::catalog(format!("unknown principal: {principal_name}")))?;
        Ok(self
            .audit
            .iter()
            .filter(|a| me.is_privileged() || a.principal_id == me.id)
            .map(|a| {
                let name = self
                    .principal_by_id(a.principal_id)
                    .map_or_else(String::new, |p| p.name.clone());
                (name, a.clone())
            })
            .collect())
    }
}

pub fn op_name(op: PhysicalOp) -> &'static str {
    match op {
        PhysicalOp::Scan => "Scan",
        PhysicalOp::Filter => "Filter",
        PhysicalOp::ContributionBound => "ContributionBound",
        PhysicalOp::Clamp => "Clamp",
        PhysicalOp::PartitionSelect => "PartitionSelect",
        PhysicalOp::DPAggregate => "DPAggregate",
    }
}

/// The five demo principals of the legacy stack, with the same budgets, plus column grants.
pub fn seed_demo(engine: &mut Engine) -> Result<()> {
    engine.add_principal("alice", &["dp_analyst"], Some(5.0))?;
    engine.add_principal("frank", &["dp_analyst"], Some(0.2))?;
    engine.add_principal("erin", &["auditor"], Some(5.0))?;
    engine.add_principal("carol", &["clinician"], None)?;
    engine.add_principal("dave", &["dba"], None)?;
    for role in ["dp_analyst", "auditor"] {
        for col in [
            "dept_id",
            "total_charge",
            "admission_type",
            "admit_ts",
            "discharge_ts",
        ] {
            engine.grant(role, "visits", col)?;
        }
    }
    Ok(())
}
