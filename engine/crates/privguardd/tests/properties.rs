//! Engine-level property tests (Phase 3.7): the budget and the noise cache under random
//! workloads, through the real facade, storage and persistence.
//!
//! * the budget never over-spends and a failed query costs nothing;
//! * the noise cache never leaks fresh noise: a question already answered is answered
//!   identically and for free, including after a restart.

mod common;
use common::*;
use proptest::prelude::*;
use std::collections::HashMap;

const SQL: [&str; 6] = [
    "SELECT COUNT(*) FROM visits",
    "SELECT SUM(total_charge) FROM visits",
    "SELECT AVG(total_charge) FROM visits",
    "SELECT COUNT(*) FROM visits GROUP BY admission_type",
    "SELECT COUNT(*) FROM visits WHERE dept_id = 2",
    "SELECT SUM(total_charge) FROM visits WHERE admission_type = 'EMERGENCY'",
];

fn step() -> impl Strategy<Value = (&'static str, usize, f64)> {
    (
        prop_oneof![Just("alice"), Just("frank")],
        0..SQL.len(),
        0.02f64..0.9,
    )
}

fn spent_and_reserved(e: &privguardd::engine::Engine, who: &str) -> (f64, f64, f64) {
    let (_, _, b) = e.balances(who).unwrap().remove(0);
    (b.spent, b.reserved, b.total)
}

proptest! {
    // Each case builds a database, so keep the case count modest.
    #![proptest_config(ProptestConfig::with_cases(12))]

    #[test]
    fn budget_never_over_spends_and_the_cache_never_leaks_fresh_noise(
        steps in prop::collection::vec(step(), 1..14)
    ) {
        let db = database();
        let mut e = open(&db);
        // (principal, sql) -> the rows first released for it.
        let mut released: HashMap<(&str, usize), Vec<privguard_core::Row>> = HashMap::new();

        for (who, i, rho) in &steps {
            let (spent_before, _, _) = spent_and_reserved(&e, who);
            let result = e.query(who, SQL[*i], *rho);
            let (spent, reserved, total) = spent_and_reserved(&e, who);

            prop_assert!(reserved.abs() < 1e-12, "{who}: reservation left open");
            prop_assert!(spent <= total + 1e-9, "{who}: spent {spent} of {total}");

            match result {
                Err(_) => prop_assert!((spent - spent_before).abs() < 1e-12, "{who}: a failed query was charged"),
                Ok(out) => match released.get(&(*who, *i)) {
                    Some(first) => {
                        prop_assert_eq!(out.status, "cache_hit");
                        prop_assert_eq!(out.rho_charged, 0.0);
                        prop_assert_eq!(&out.rows, first);
                        prop_assert!((spent - spent_before).abs() < 1e-12);
                    }
                    None => {
                        prop_assert_eq!(out.status, "executed");
                        prop_assert!((spent - spent_before - out.rho_charged).abs() < 1e-9);
                        released.insert((*who, *i), out.rows);
                    }
                },
            }
        }

        // Restart: the ledger replays cleanly and every released answer is still free and identical.
        let balances: Vec<_> = ["alice", "frank"].iter().map(|w| spent_and_reserved(&e, w)).collect();
        drop(e);
        let mut e = open(&db);
        for (w, b) in ["alice", "frank"].iter().zip(&balances) {
            prop_assert_eq!(&spent_and_reserved(&e, w), b);
        }
        for ((who, i), rows) in &released {
            let out = e.query(who, SQL[*i], 0.5).unwrap();
            prop_assert_eq!(out.status, "cache_hit");
            prop_assert_eq!(&out.rows, rows);
        }
    }
}
