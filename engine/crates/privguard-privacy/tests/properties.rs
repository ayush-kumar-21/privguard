//! Property tests for the budget ledger (Phase 3.7): random reserve/commit/abort sequences must
//! never over-spend, never fork the hash chain, and never survive tampering.

use privguard_privacy::ledger::{BudgetLedger, ChargeMeta, EntryKind, LedgerEntry};
use proptest::prelude::*;

const ACCT: u64 = 1;
const TOTAL: f64 = 3.0;
const TOL: f64 = 1e-9;

#[derive(Debug, Clone)]
enum Op {
    Reserve { q: u64, rho: f64 },
    Commit { q: u64 },
    Abort { q: u64 },
}

fn op() -> impl Strategy<Value = Op> {
    prop_oneof![
        3 => (0u64..6, 0.01f64..1.6).prop_map(|(q, rho)| Op::Reserve { q, rho }),
        2 => (0u64..6).prop_map(|q| Op::Commit { q }),
        1 => (0u64..6).prop_map(|q| Op::Abort { q }),
    ]
}

fn run(ops: &[Op]) -> BudgetLedger {
    let mut l = BudgetLedger::new();
    l.open_account(ACCT, TOTAL).unwrap();
    for o in ops {
        // Protocol violations (double reserve, settling nothing, over-budget) are expected
        // rejections; the properties are about the state they leave behind.
        let _ = match o {
            Op::Reserve { q, rho } => l.reserve(ACCT, *q, *rho, ChargeMeta::default()),
            Op::Commit { q } => l.commit(ACCT, *q),
            Op::Abort { q } => l.abort(ACCT, *q),
        };
    }
    l
}

proptest! {
    /// Budget never over-spends: after every prefix, spent + reserved <= total, nothing is
    /// negative, and `spent` equals the sum of committed charges.
    #[test]
    fn budget_never_over_spends(ops in prop::collection::vec(op(), 0..60)) {
        let mut l = BudgetLedger::new();
        l.open_account(ACCT, TOTAL).unwrap();
        for o in &ops {
            let _ = match o {
                Op::Reserve { q, rho } => l.reserve(ACCT, *q, *rho, ChargeMeta::default()),
                Op::Commit { q } => l.commit(ACCT, *q),
                Op::Abort { q } => l.abort(ACCT, *q),
            };
            let b = l.balance(ACCT).unwrap();
            prop_assert!(b.spent >= 0.0 && b.reserved >= 0.0);
            prop_assert!(b.spent + b.reserved <= TOTAL + TOL, "over-spent: {b:?}");
        }
        let committed: f64 = l.entries(ACCT).unwrap().iter()
            .filter(|e| e.kind == EntryKind::Commit).map(|e| e.rho).sum();
        let b = l.balance(ACCT).unwrap();
        prop_assert!((b.spent - committed).abs() <= TOL, "spent {} vs committed {committed}", b.spent);
        prop_assert!(l.verify().is_ok());
    }

    /// A reservation that fails changes nothing: the entry count and balances are untouched.
    #[test]
    fn a_rejected_reserve_leaves_no_trace(ops in prop::collection::vec(op(), 0..40), rho in 0.01f64..10.0) {
        let mut l = run(&ops);
        let (before_n, before_b) = (l.entries(ACCT).unwrap().len(), l.balance(ACCT).unwrap());
        if l.reserve(ACCT, 99, rho, ChargeMeta::default()).is_err() {
            prop_assert_eq!(l.entries(ACCT).unwrap().len(), before_n);
            prop_assert_eq!(l.balance(ACCT).unwrap(), before_b);
        }
    }

    /// The chain never forks: one linear sequence, each entry pointing at its predecessor, the
    /// head is the last hash, and replaying the same operations gives the same head.
    #[test]
    fn the_chain_never_forks(ops in prop::collection::vec(op(), 1..60)) {
        let l = run(&ops);
        let entries = l.entries(ACCT).unwrap();
        for (i, e) in entries.iter().enumerate() {
            prop_assert_eq!(e.seq, i as u64);
            if i > 0 {
                prop_assert_eq!(e.prev_hash, entries[i - 1].hash);
            }
        }
        if let Some(last) = entries.last() {
            prop_assert_eq!(l.head_hash(ACCT).unwrap(), last.hash);
        }
        prop_assert_eq!(run(&ops).head_hash(ACCT).unwrap(), l.head_hash(ACCT).unwrap());
        // Persist and restore: the same chain, the same head.
        let restored = BudgetLedger::restore(&l.accounts(), entries.to_vec()).unwrap();
        prop_assert_eq!(restored.head_hash(ACCT).unwrap(), l.head_hash(ACCT).unwrap());
        prop_assert_eq!(restored.balance(ACCT).unwrap(), l.balance(ACCT).unwrap());
    }

    /// Any edit to a stored entry (rho, kind, query, hashes, sequence) is refused on restore.
    #[test]
    fn any_tampering_is_detected(
        ops in prop::collection::vec(op(), 1..40),
        which in any::<prop::sample::Index>(),
        how in 0u8..6,
    ) {
        // Start with one valid reservation so there is always an entry to tamper with.
        let mut all = vec![Op::Reserve { q: 100, rho: 0.1 }];
        all.extend(ops);
        let l = run(&all);
        let mut entries: Vec<LedgerEntry> = l.entries(ACCT).unwrap().to_vec();
        let idx = which.index(entries.len());
        let e = &mut entries[idx];
        match how {
            0 => e.rho = f64::from_bits(e.rho.to_bits() ^ 1),
            1 => e.kind = if e.kind == EntryKind::Reserve { EntryKind::Abort } else { EntryKind::Reserve },
            2 => e.query_id ^= 1,
            3 => e.hash[0] ^= 1,
            4 => e.prev_hash[0] ^= 1,
            _ => e.seq += 1,
        }
        prop_assert!(BudgetLedger::restore(&l.accounts(), entries).is_err());
    }
}
