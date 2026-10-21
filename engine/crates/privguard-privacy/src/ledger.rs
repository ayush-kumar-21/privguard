//! The budget ledger. `SPEC.md` §6 (`IMPLEMENTATION_PLAN.md` 3.4).
//!
//! Append-only and hash-chained, one chain per budget account: budget as system state, like a
//! WAL. Every query follows `Reserve(rho)` -> execute -> `Commit` or `Abort`, and **no data
//! page is read before the reservation succeeds** (the caller's side of the contract).
//!
//! ```text
//! hash = blake3( seq ‖ account_id ‖ query_id ‖ kind ‖ rho_bits ‖ prev_hash )
//! ```
//!
//! All integers are big-endian, `kind` is one byte (`Reserve` 0, `Commit` 1, `Abort` 2) and
//! `rho_bits` is `f64::to_bits`. The first entry of a chain uses [`GENESIS_HASH`] as `prev_hash`.
//!
//! `verify` recomputes every hash and replays the protocol, so an edited, dropped, reordered or
//! forged entry is caught. Two limits are inherent to a bare hash chain: the metadata fields
//! (`mechanism`, `sensitivity`, `scale`, `eps_equiv`) are not covered by the formula above, and
//! an attacker who rewrites the chain from some point to its end leaves it self-consistent.
//! Publishing `head_hash` somewhere the attacker cannot reach closes the second gap.

use crate::fingerprint::QueryFingerprint;
use privguard_core::{Error, Result, Row, Value};
use std::collections::BTreeMap;

/// Hash of the (nonexistent) entry before a chain's first. Same as
/// `privguard_crypto::GENESIS_HASH`; repeated here so this crate need not depend on that one.
pub const GENESIS_HASH: [u8; 32] = [0u8; 32];

/// Slack for floating-point accumulation when comparing sums of rho with the allowance.
const EPS: f64 = 1e-12;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryKind {
    Reserve,
    Commit,
    Abort,
}

impl EntryKind {
    const fn tag(self) -> u8 {
        match self {
            EntryKind::Reserve => 0,
            EntryKind::Commit => 1,
            EntryKind::Abort => 2,
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            EntryKind::Reserve => "reserve",
            EntryKind::Commit => "commit",
            EntryKind::Abort => "abort",
        }
    }

    fn parse(s: &str) -> Result<Self> {
        match s {
            "reserve" => Ok(EntryKind::Reserve),
            "commit" => Ok(EntryKind::Commit),
            "abort" => Ok(EntryKind::Abort),
            other => Err(Error::engine(format!("unknown ledger entry kind: {other}"))),
        }
    }
}

/// How a charge was calibrated; carried on every entry but not covered by the hash.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ChargeMeta {
    pub mechanism: String,
    pub sensitivity: f64,
    pub scale: f64,
    pub eps_equiv: f64,
}

/// One ledger row (`sys_budget_ledger`).
#[derive(Debug, Clone, PartialEq)]
pub struct LedgerEntry {
    pub seq: u64,
    pub account_id: u64,
    pub query_id: u64,
    pub kind: EntryKind,
    pub rho: f64,
    pub meta: ChargeMeta,
    pub prev_hash: [u8; 32],
    pub hash: [u8; 32],
}

impl LedgerEntry {
    fn compute_hash(&self) -> [u8; 32] {
        let mut h = blake3::Hasher::new();
        h.update(&self.seq.to_be_bytes());
        h.update(&self.account_id.to_be_bytes());
        h.update(&self.query_id.to_be_bytes());
        h.update(&[self.kind.tag()]);
        h.update(&self.rho.to_bits().to_be_bytes());
        h.update(&self.prev_hash);
        *h.finalize().as_bytes()
    }

    /// The row stored in `sys_budget_ledger` (schema in `privguard_catalog::schema`).
    pub fn to_row(&self) -> Row {
        vec![
            Value::I64(self.seq as i64),
            Value::I64(self.account_id as i64),
            Value::I64(self.query_id as i64),
            Value::Text(self.kind.as_str().to_string()),
            Value::F64(self.rho),
            Value::Text(self.meta.mechanism.clone()),
            Value::F64(self.meta.sensitivity),
            Value::F64(self.meta.scale),
            Value::F64(self.meta.eps_equiv),
            Value::Text(QueryFingerprint::from_digest(self.prev_hash).to_hex()),
            Value::Text(QueryFingerprint::from_digest(self.hash).to_hex()),
        ]
    }

    pub fn from_row(row: &Row) -> Result<Self> {
        let bad = || Error::engine("malformed sys_budget_ledger row");
        let int = |i: usize| match row.get(i) {
            Some(Value::I64(v)) => Ok(*v as u64),
            _ => Err(bad()),
        };
        let float = |i: usize| match row.get(i) {
            Some(Value::F64(v)) => Ok(*v),
            _ => Err(bad()),
        };
        let text = |i: usize| match row.get(i) {
            Some(Value::Text(s)) => Ok(s.as_str()),
            _ => Err(bad()),
        };
        Ok(LedgerEntry {
            seq: int(0)?,
            account_id: int(1)?,
            query_id: int(2)?,
            kind: EntryKind::parse(text(3)?)?,
            rho: float(4)?,
            meta: ChargeMeta {
                mechanism: text(5)?.to_string(),
                sensitivity: float(6)?,
                scale: float(7)?,
                eps_equiv: float(8)?,
            },
            prev_hash: *QueryFingerprint::from_hex(text(9)?)?.as_bytes(),
            hash: *QueryFingerprint::from_hex(text(10)?)?.as_bytes(),
        })
    }
}

/// An account's balances, all in rho.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Balance {
    pub total: f64,
    pub spent: f64,
    pub reserved: f64,
}

impl Balance {
    pub fn remaining(&self) -> f64 {
        (self.total - self.spent - self.reserved).max(0.0)
    }
}

#[derive(Debug, Clone)]
struct Account {
    total: f64,
    spent: f64,
    reserved: f64,
    entries: Vec<LedgerEntry>,
    /// Open reservations: query id -> (rho, metadata of the reserve entry).
    open: BTreeMap<u64, (f64, ChargeMeta)>,
}

impl Account {
    fn new(total: f64) -> Self {
        Account {
            total,
            spent: 0.0,
            reserved: 0.0,
            entries: Vec::new(),
            open: BTreeMap::new(),
        }
    }

    fn head(&self) -> [u8; 32] {
        self.entries.last().map_or(GENESIS_HASH, |e| e.hash)
    }

    /// Apply `entry`'s effect on the balances, enforcing the protocol. Shared by live
    /// operation and by replay in [`BudgetLedger::restore`].
    fn apply(&mut self, e: &LedgerEntry) -> Result<()> {
        match e.kind {
            EntryKind::Reserve => {
                if !(e.rho.is_finite() && e.rho > 0.0) {
                    return Err(Error::engine("reserve of a non-positive or non-finite rho"));
                }
                if self.open.contains_key(&e.query_id) {
                    return Err(Error::engine(format!(
                        "query {} already reserved",
                        e.query_id
                    )));
                }
                if self.spent + self.reserved + e.rho > self.total + EPS {
                    return Err(Error::budget(format!(
                        "reserving rho={} exceeds the allowance: spent {}, reserved {}, total {}",
                        e.rho, self.spent, self.reserved, self.total
                    )));
                }
                self.reserved += e.rho;
                self.open.insert(e.query_id, (e.rho, e.meta.clone()));
            }
            EntryKind::Commit | EntryKind::Abort => {
                let (rho, _) = self.open.remove(&e.query_id).ok_or_else(|| {
                    Error::engine(format!("query {} has no open reservation", e.query_id))
                })?;
                if rho.to_bits() != e.rho.to_bits() {
                    return Err(Error::engine(format!(
                        "query {} settles rho={} but reserved {rho}",
                        e.query_id, e.rho
                    )));
                }
                self.reserved = (self.reserved - rho).max(0.0);
                if e.kind == EntryKind::Commit {
                    self.spent += rho;
                }
            }
        }
        Ok(())
    }
}

/// All accounts' chains.
#[derive(Debug, Clone, Default)]
pub struct BudgetLedger {
    accounts: BTreeMap<u64, Account>,
}

impl BudgetLedger {
    pub fn new() -> Self {
        Self::default()
    }

    /// Create an account with an allowance of `rho_total`.
    pub fn open_account(&mut self, account_id: u64, rho_total: f64) -> Result<()> {
        if !(rho_total.is_finite() && rho_total > 0.0) {
            return Err(Error::policy(
                "a budget allowance must be positive and finite",
            ));
        }
        if self.accounts.contains_key(&account_id) {
            return Err(Error::engine(format!(
                "account {account_id} already exists"
            )));
        }
        self.accounts.insert(account_id, Account::new(rho_total));
        Ok(())
    }

    fn account(&self, id: u64) -> Result<&Account> {
        self.accounts
            .get(&id)
            .ok_or_else(|| Error::catalog(format!("unknown budget account {id}")))
    }

    fn append(
        &mut self,
        account_id: u64,
        query_id: u64,
        kind: EntryKind,
        rho: f64,
        meta: ChargeMeta,
    ) -> Result<()> {
        let acct = self
            .accounts
            .get_mut(&account_id)
            .ok_or_else(|| Error::catalog(format!("unknown budget account {account_id}")))?;
        let mut entry = LedgerEntry {
            seq: acct.entries.len() as u64,
            account_id,
            query_id,
            kind,
            rho,
            meta,
            prev_hash: acct.head(),
            hash: [0; 32],
        };
        entry.hash = entry.compute_hash();
        acct.apply(&entry)?;
        acct.entries.push(entry);
        Ok(())
    }

    /// Hold `rho` for `query_id`. Fails with `BudgetExhausted` if the allowance cannot cover it.
    pub fn reserve(
        &mut self,
        account_id: u64,
        query_id: u64,
        rho: f64,
        meta: ChargeMeta,
    ) -> Result<()> {
        self.append(account_id, query_id, EntryKind::Reserve, rho, meta)
    }

    fn settle(&mut self, account_id: u64, query_id: u64, kind: EntryKind) -> Result<()> {
        let (rho, meta) = self
            .account(account_id)?
            .open
            .get(&query_id)
            .cloned()
            .ok_or_else(|| Error::engine(format!("query {query_id} has no open reservation")))?;
        self.append(account_id, query_id, kind, rho, meta)
    }

    /// Turn the reservation into spent budget.
    pub fn commit(&mut self, account_id: u64, query_id: u64) -> Result<()> {
        self.settle(account_id, query_id, EntryKind::Commit)
    }

    /// Release the reservation; nothing is spent.
    pub fn abort(&mut self, account_id: u64, query_id: u64) -> Result<()> {
        self.settle(account_id, query_id, EntryKind::Abort)
    }

    /// Abort every open reservation of the account (crash recovery at startup). Returns how
    /// many were released.
    pub fn abort_dangling(&mut self, account_id: u64) -> Result<usize> {
        let open: Vec<u64> = self.account(account_id)?.open.keys().copied().collect();
        for q in &open {
            self.abort(account_id, *q)?;
        }
        Ok(open.len())
    }

    pub fn balance(&self, account_id: u64) -> Result<Balance> {
        let a = self.account(account_id)?;
        Ok(Balance {
            total: a.total,
            spent: a.spent,
            reserved: a.reserved,
        })
    }

    pub fn entries(&self, account_id: u64) -> Result<&[LedgerEntry]> {
        Ok(&self.account(account_id)?.entries)
    }

    /// Hash of the newest entry; anchor this outside the ledger to detect tail rewrites.
    pub fn head_hash(&self, account_id: u64) -> Result<[u8; 32]> {
        Ok(self.account(account_id)?.head())
    }

    /// Open accounts as `(account_id, rho_total)`.
    pub fn accounts(&self) -> Vec<(u64, f64)> {
        self.accounts.iter().map(|(id, a)| (*id, a.total)).collect()
    }

    /// Recompute every chain and replay the reserve/commit/abort protocol from the start.
    pub fn verify(&self) -> Result<()> {
        for (id, acct) in &self.accounts {
            let mut replay = Account::new(acct.total);
            let mut prev = GENESIS_HASH;
            for (i, e) in acct.entries.iter().enumerate() {
                let tampered = |what: &str| {
                    Error::engine(format!(
                        "ledger tampering detected: account {id} entry {i}: {what}"
                    ))
                };
                if e.account_id != *id || e.seq != i as u64 {
                    return Err(tampered("wrong account or sequence number"));
                }
                if e.prev_hash != prev {
                    return Err(tampered("prev_hash does not match the previous entry"));
                }
                if e.hash != e.compute_hash() {
                    return Err(tampered("hash does not match the entry contents"));
                }
                replay
                    .apply(e)
                    .map_err(|err| tampered(&format!("protocol violation: {err}")))?;
                prev = e.hash;
            }
            if replay.spent.to_bits() != acct.spent.to_bits()
                || replay.reserved.to_bits() != acct.reserved.to_bits()
            {
                return Err(Error::engine(format!(
                    "ledger tampering detected: account {id} balances disagree with its entries"
                )));
            }
        }
        Ok(())
    }

    /// Rebuild a ledger from persisted state: the accounts' allowances plus every entry, in
    /// any order across accounts but chain order within one. Fails if anything does not verify.
    pub fn restore(accounts: &[(u64, f64)], entries: Vec<LedgerEntry>) -> Result<Self> {
        let mut ledger = BudgetLedger::new();
        for (id, total) in accounts {
            ledger.open_account(*id, *total)?;
        }
        for e in entries {
            let acct = ledger.accounts.get_mut(&e.account_id).ok_or_else(|| {
                Error::engine(format!("ledger entry for unknown account {}", e.account_id))
            })?;
            // Replay for balances; hashes and linkage are checked by `verify` below.
            let index = acct.entries.len();
            acct.apply(&e).map_err(|err| {
                Error::engine(format!(
                    "ledger tampering detected: account {} entry {index}: protocol violation: {err}",
                    e.account_id
                ))
            })?;
            acct.entries.push(e);
        }
        ledger.verify()?;
        Ok(ledger)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ACCT: u64 = 7;

    fn meta() -> ChargeMeta {
        ChargeMeta {
            mechanism: "laplace".into(),
            sensitivity: 5.0,
            scale: 7.0,
            eps_equiv: 3.5,
        }
    }

    fn ledger(total: f64) -> BudgetLedger {
        let mut l = BudgetLedger::new();
        l.open_account(ACCT, total).unwrap();
        l
    }

    #[test]
    fn reserve_then_commit_spends_and_abort_refunds() {
        let mut l = ledger(1.0);
        l.reserve(ACCT, 1, 0.25, meta()).unwrap();
        let b = l.balance(ACCT).unwrap();
        assert_eq!((b.spent, b.reserved, b.remaining()), (0.0, 0.25, 0.75));
        l.commit(ACCT, 1).unwrap();
        let b = l.balance(ACCT).unwrap();
        assert_eq!((b.spent, b.reserved), (0.25, 0.0));

        l.reserve(ACCT, 2, 0.5, meta()).unwrap();
        l.abort(ACCT, 2).unwrap();
        let b = l.balance(ACCT).unwrap();
        assert_eq!((b.spent, b.reserved, b.remaining()), (0.25, 0.0, 0.75));
        l.verify().unwrap();
    }

    #[test]
    fn a_reservation_beyond_the_allowance_is_refused_and_leaves_no_trace() {
        let mut l = ledger(1.0);
        l.reserve(ACCT, 1, 0.6, meta()).unwrap();
        let err = l.reserve(ACCT, 2, 0.5, meta()).unwrap_err();
        assert_eq!(err.sqlstate().code(), "53400");
        assert_eq!(l.entries(ACCT).unwrap().len(), 1);
        // Held budget counts: even after the first commits, 0.6 is spent.
        l.commit(ACCT, 1).unwrap();
        assert!(l.reserve(ACCT, 3, 0.5, meta()).is_err());
        l.reserve(ACCT, 4, 0.4, meta()).unwrap();
    }

    #[test]
    fn spending_exactly_the_allowance_in_small_steps_works() {
        let mut l = ledger(0.5);
        for q in 0..5 {
            l.reserve(ACCT, q, 0.1, meta()).unwrap();
            l.commit(ACCT, q).unwrap();
        }
        assert!(l.reserve(ACCT, 99, 0.01, meta()).is_err());
        l.verify().unwrap();
    }

    #[test]
    fn protocol_violations_are_rejected() {
        let mut l = ledger(1.0);
        assert!(l.commit(ACCT, 1).is_err(), "commit without reserve");
        assert!(l.abort(ACCT, 1).is_err(), "abort without reserve");
        l.reserve(ACCT, 1, 0.1, meta()).unwrap();
        assert!(l.reserve(ACCT, 1, 0.1, meta()).is_err(), "double reserve");
        l.commit(ACCT, 1).unwrap();
        assert!(l.commit(ACCT, 1).is_err(), "double commit");
        assert!(l.reserve(ACCT, 2, 0.0, meta()).is_err());
        assert!(l.reserve(ACCT, 2, -1.0, meta()).is_err());
        assert!(l.reserve(ACCT, 2, f64::NAN, meta()).is_err());
        assert!(l.reserve(99, 2, 0.1, meta()).is_err(), "unknown account");
        assert!(l.open_account(ACCT, 1.0).is_err(), "duplicate account");
        assert!(l.open_account(8, 0.0).is_err());
    }

    #[test]
    fn dangling_reservations_are_released_on_recovery() {
        let mut l = ledger(1.0);
        l.reserve(ACCT, 1, 0.3, meta()).unwrap();
        l.reserve(ACCT, 2, 0.2, meta()).unwrap();
        l.commit(ACCT, 2).unwrap();
        assert_eq!(l.abort_dangling(ACCT).unwrap(), 1);
        let b = l.balance(ACCT).unwrap();
        assert_eq!((b.spent, b.reserved), (0.2, 0.0));
        assert_eq!(l.abort_dangling(ACCT).unwrap(), 0);
        l.verify().unwrap();
    }

    #[test]
    fn the_chain_starts_at_genesis_and_links_each_entry() {
        let mut l = ledger(1.0);
        l.reserve(ACCT, 1, 0.1, meta()).unwrap();
        l.commit(ACCT, 1).unwrap();
        let e = l.entries(ACCT).unwrap();
        assert_eq!(e[0].prev_hash, GENESIS_HASH);
        assert_eq!(e[1].prev_hash, e[0].hash);
        assert_eq!((e[0].seq, e[1].seq), (0, 1));
        assert_eq!(l.head_hash(ACCT).unwrap(), e[1].hash);
        assert_ne!(e[0].hash, e[1].hash);
    }

    #[test]
    fn hash_follows_the_spec_formula() {
        let mut l = ledger(1.0);
        l.reserve(ACCT, 42, 0.125, meta()).unwrap();
        let e = &l.entries(ACCT).unwrap()[0];
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&0u64.to_be_bytes());
        bytes.extend_from_slice(&ACCT.to_be_bytes());
        bytes.extend_from_slice(&42u64.to_be_bytes());
        bytes.push(0);
        bytes.extend_from_slice(&0.125f64.to_bits().to_be_bytes());
        bytes.extend_from_slice(&GENESIS_HASH);
        assert_eq!(e.hash, *blake3::hash(&bytes).as_bytes());
    }

    fn busy_ledger() -> BudgetLedger {
        let mut l = ledger(10.0);
        for q in 0..6 {
            l.reserve(ACCT, q, 0.5, meta()).unwrap();
            if q % 2 == 0 {
                l.commit(ACCT, q).unwrap();
            } else {
                l.abort(ACCT, q).unwrap();
            }
        }
        l
    }

    #[test]
    fn editing_a_historical_entry_is_detected() {
        let mut l = busy_ledger();
        l.verify().unwrap();
        // Make an old charge look cheaper.
        l.accounts.get_mut(&ACCT).unwrap().entries[2].rho = 0.01;
        let err = l.verify().unwrap_err().to_string();
        assert!(err.contains("tampering detected"), "{err}");
    }

    #[test]
    fn dropping_or_reordering_entries_is_detected() {
        let mut l = busy_ledger();
        l.accounts.get_mut(&ACCT).unwrap().entries.remove(3);
        assert!(l.verify().is_err());

        let mut l = busy_ledger();
        l.accounts.get_mut(&ACCT).unwrap().entries.swap(4, 5);
        assert!(l.verify().is_err());
    }

    #[test]
    fn rehashing_an_edit_still_breaks_every_later_link() {
        let mut l = busy_ledger();
        let acct = l.accounts.get_mut(&ACCT).unwrap();
        // Rewrite a reserve/settle pair consistently, recomputing both hashes...
        acct.entries[0].rho = 0.4;
        acct.entries[0].hash = acct.entries[0].compute_hash();
        acct.entries[1].rho = 0.4;
        acct.entries[1].prev_hash = acct.entries[0].hash;
        acct.entries[1].hash = acct.entries[1].compute_hash();
        // ...entry 2's prev_hash still points at the old hash of entry 1.
        let err = l.verify().unwrap_err().to_string();
        assert!(err.contains("entry 2"), "{err}");
    }

    #[test]
    fn a_forged_balance_is_detected() {
        let mut l = busy_ledger();
        l.accounts.get_mut(&ACCT).unwrap().spent = 0.0;
        assert!(l.verify().is_err());
    }

    #[test]
    fn rows_round_trip_and_a_restored_ledger_verifies() {
        let l = busy_ledger();
        let rows: Vec<Row> = l
            .entries(ACCT)
            .unwrap()
            .iter()
            .map(|e| e.to_row())
            .collect();
        let entries: Vec<LedgerEntry> = rows
            .iter()
            .map(|r| LedgerEntry::from_row(r).unwrap())
            .collect();
        assert_eq!(entries, l.entries(ACCT).unwrap());

        let mut restored = BudgetLedger::restore(&l.accounts(), entries.clone()).unwrap();
        assert_eq!(restored.balance(ACCT).unwrap(), l.balance(ACCT).unwrap());
        assert_eq!(
            restored.head_hash(ACCT).unwrap(),
            l.head_hash(ACCT).unwrap()
        );
        // And it keeps chaining from where it left off.
        restored.reserve(ACCT, 100, 0.5, meta()).unwrap();
        restored.verify().unwrap();
    }

    #[test]
    fn restoring_tampered_rows_fails() {
        let l = busy_ledger();
        let mut rows: Vec<Row> = l
            .entries(ACCT)
            .unwrap()
            .iter()
            .map(|e| e.to_row())
            .collect();
        rows[4][4] = Value::F64(0.001); // rho column
        let entries: Vec<LedgerEntry> = rows
            .iter()
            .map(|r| LedgerEntry::from_row(r).unwrap())
            .collect();
        assert!(BudgetLedger::restore(&l.accounts(), entries).is_err());

        let bad = vec![Value::Text("nope".into())];
        assert!(LedgerEntry::from_row(&bad).is_err());
    }

    #[test]
    fn accounts_are_independent_chains() {
        let mut l = ledger(1.0);
        l.open_account(8, 1.0).unwrap();
        l.reserve(ACCT, 1, 0.9, meta()).unwrap();
        l.reserve(8, 1, 0.9, meta()).unwrap();
        assert_eq!(l.entries(8).unwrap()[0].prev_hash, GENESIS_HASH);
        assert_ne!(
            l.entries(ACCT).unwrap()[0].hash,
            l.entries(8).unwrap()[0].hash
        );
        l.verify().unwrap();
    }
}
