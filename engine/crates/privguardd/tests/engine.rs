//! The engine facade end to end: golden dataset -> loader -> heap files -> `Engine::query`.

mod common;
use common::*;
use privguard_catalog::{read_sys_rows, write_sys_rows};
use privguard_core::json::{self, Json};
use privguard_core::{Error, Value};
use privguardd::engine::Engine;

const COUNT: &str = "SELECT COUNT(*) FROM visits";

#[test]
fn answers_equal_the_oracles_committed_noisy_answers() {
    let db = database();
    let mut e = open(&db);

    let out = e.query("alice", COUNT, 0.5).unwrap();
    let want = expected("count_all");
    assert_eq!(out.status, "executed");
    assert_eq!(out.mechanism, "discrete_laplace");
    let want_n = want.get("rows").unwrap().as_array().unwrap()[0]
        .get("value")
        .unwrap()
        .as_i64()
        .unwrap();
    assert_eq!(out.rows, vec![vec![Value::I64(want_n)]], "count_all");

    let out = e
        .query("alice", "SELECT SUM(total_charge) FROM visits", 0.5)
        .unwrap();
    let want = expected("sum_all");
    let want_v = want.get("rows").unwrap().as_array().unwrap()[0]
        .get("value")
        .unwrap()
        .as_f64()
        .unwrap();
    let Value::F64(got) = out.rows[0][0] else {
        panic!()
    };
    assert!((got - want_v).abs() <= 1.01e-4, "{got} vs {want_v}");

    let out = e
        .query(
            "alice",
            "SELECT COUNT(*) FROM visits GROUP BY admission_type",
            0.5,
        )
        .unwrap();
    let want = expected("count_by_admission");
    assert_eq!(out.mechanism, "gaussian");
    assert_eq!(
        out.suppressed_groups as f64,
        want.get("suppressed_groups").unwrap().as_f64().unwrap()
    );
    let want_rows = want.get("rows").unwrap().as_array().unwrap();
    assert_eq!(out.rows.len(), want_rows.len());
    for (got, w) in out.rows.iter().zip(want_rows) {
        assert_eq!(got[0], w.get("admission_type").unwrap().to_value().unwrap());
    }
    assert_eq!(
        out.columns.iter().map(|c| c.0.as_str()).collect::<Vec<_>>(),
        ["admission_type", "value"]
    );
}

#[test]
fn a_query_is_charged_to_the_ledger_and_a_repeat_is_free() {
    let db = database();
    let mut e = open(&db);
    let first = e.query("alice", COUNT, 0.05).unwrap();
    assert_eq!(first.status, "executed");
    assert_eq!(first.rho_charged, 0.05);
    assert!((first.budget.spent - 0.05).abs() < 1e-12);
    assert_eq!(first.budget.reserved, 0.0);

    // Same question, different spelling: the stored answer, at zero cost.
    let again = e
        .query("alice", "select   count(*)  from VISITS", 0.05)
        .unwrap();
    assert_eq!(again.status, "cache_hit");
    assert_eq!(again.rho_charged, 0.0);
    assert_eq!(again.epsilon_charged, 0.0);
    assert_eq!(again.rows, first.rows);
    assert_eq!(again.budget.spent, first.budget.spent);

    // Even a different rho cannot buy fresh noise for the same question.
    let pricier = e.query("alice", COUNT, 1.0).unwrap();
    assert_eq!(pricier.status, "cache_hit");
    assert_eq!(pricier.rows, first.rows);

    // The chain has exactly one reserve and one commit.
    let v = e.verify_ledger("alice").unwrap().unwrap();
    assert!(v.ok);
    assert_eq!(v.entries, 2);
}

#[test]
fn the_cache_is_per_principal() {
    let db = database();
    let mut e = open(&db);
    let a = e.query("alice", COUNT, 0.05).unwrap();
    let r = e.query("erin", COUNT, 0.05).unwrap();
    assert_eq!(r.status, "executed");
    assert_eq!(r.rho_charged, 0.05);
    assert_eq!(a.rho_charged, 0.05);
    // Different principal ids seed different noise.
    assert_ne!(a.rows, r.rows);
}

#[test]
fn an_exhausted_budget_is_refused_and_spends_nothing() {
    let db = database();
    let mut e = open(&db);
    e.query("frank", COUNT, 0.15).unwrap(); // frank holds 0.2
    let err = e
        .query("frank", "SELECT SUM(total_charge) FROM visits", 0.15)
        .unwrap_err();
    assert!(matches!(err, Error::BudgetExhausted(_)), "{err}");
    assert_eq!(err.sqlstate().code(), "53400");
    let (_, bal) = e.balance("frank").unwrap().unwrap();
    assert!((bal.spent - 0.15).abs() < 1e-12);
    assert_eq!(bal.reserved, 0.0);

    let rows = e.audit_rows("frank").unwrap();
    assert_eq!(
        rows.iter()
            .map(|(_, a)| a.status.as_str())
            .collect::<Vec<_>>(),
        ["executed", "rejected_budget"]
    );
    assert!(e.verify_ledger("frank").unwrap().unwrap().ok);
}

#[test]
fn rbac_refuses_columns_without_a_grant_and_principals_without_an_account() {
    let db = database();
    let mut e = open(&db);
    assert!(e.revoke("dp_analyst", "visits", "dept_id").unwrap());
    let err = e
        .query(
            "alice",
            "SELECT COUNT(*) FROM visits WHERE dept_id = 1",
            0.05,
        )
        .unwrap_err();
    assert_eq!(err.sqlstate().code(), "42501");
    assert!(err.to_string().contains("visits.dept_id"), "{err}");

    // carol is a clinician: no grants on visits, no budget account.
    assert_eq!(
        e.query("carol", COUNT, 0.05).unwrap_err().sqlstate().code(),
        "42501"
    );
    // dave is a dba with no account either.
    assert!(e.query("dave", COUNT, 0.05).is_err());
    // Neither refusal spent anything for alice.
    let (_, bal) = e.balance("alice").unwrap().unwrap();
    assert_eq!(bal.spent, 0.0);
    let rejected = e
        .audit_rows("erin")
        .unwrap()
        .iter()
        .filter(|(_, a)| a.status == "rejected_policy")
        .count();
    assert_eq!(rejected, 3);
}

#[test]
fn binder_errors_use_the_spec_error_classes() {
    let db = database();
    let mut e = open(&db);
    let mut code = |sql: &str| e.query("alice", sql, 0.05).unwrap_err().sqlstate().code();
    assert_eq!(code("DROP TABLE visits"), "42601");
    assert_eq!(
        code("SELECT COUNT(*) FROM visits WHERE dept_id = 1 OR dept_id = 2"),
        "42601"
    );
    assert_eq!(code("SELECT COUNT(*) FROM nowhere"), "42P01");
    assert_eq!(code("SELECT COUNT(*) FROM patients"), "42501");
    assert_eq!(
        code("SELECT COUNT(*) FROM visits WHERE patient_id = 7"),
        "42501"
    );
}

#[test]
fn rho_must_be_positive_and_bounded() {
    let db = database();
    let mut e = open(&db);
    for rho in [0.0, -1.0, f64::NAN, f64::INFINITY, 5.01] {
        assert!(e.query("alice", COUNT, rho).is_err(), "{rho}");
    }
    assert!(e.query("nobody", COUNT, 0.05).is_err());
}

#[test]
fn near_duplicate_questions_are_refused_and_the_hold_is_released() {
    let db = database();
    let mut e = open(&db);
    e.query(
        "alice",
        "SELECT COUNT(*) FROM visits WHERE total_charge <= 1000001.0",
        0.05,
    )
    .unwrap();
    // A tautology that changes the fingerprint but not the rows: the repeat-and-average attack.
    let err = e
        .query(
            "alice",
            "SELECT COUNT(*) FROM visits WHERE total_charge <= 1000002.0",
            0.05,
        )
        .unwrap_err();
    assert_eq!(err.sqlstate().code(), "42501");
    assert!(err.to_string().contains("overlaps"), "{err}");

    let (_, bal) = e.balance("alice").unwrap().unwrap();
    assert!(
        (bal.spent - 0.05).abs() < 1e-12,
        "the refused query spent nothing"
    );
    assert_eq!(bal.reserved, 0.0);
    // Reserve+commit for the first, reserve+abort for the second.
    let v = e.verify_ledger("alice").unwrap().unwrap();
    assert!(v.ok);
    assert_eq!(v.entries, 4);
}

#[test]
fn a_differencing_pair_is_refused() {
    let db = database();
    let mut e = open(&db);
    e.query("alice", COUNT, 0.05).unwrap();
    // visit 1's row is the only one with this exact charge (114.84): Q(D) vs Q(D minus x).
    let err = e
        .query(
            "alice",
            "SELECT COUNT(*) FROM visits WHERE total_charge <> 114.84",
            0.05,
        )
        .unwrap_err();
    assert!(err.to_string().contains("overlaps"), "{err}");
}

#[test]
fn the_same_predicates_with_another_aggregate_are_allowed() {
    let db = database();
    let mut e = open(&db);
    e.query(
        "alice",
        "SELECT COUNT(*) FROM visits WHERE dept_id = 1",
        0.05,
    )
    .unwrap();
    let r = e
        .query(
            "alice",
            "SELECT SUM(total_charge) FROM visits WHERE dept_id = 1",
            0.05,
        )
        .unwrap();
    assert_eq!(r.status, "executed");
}

#[test]
fn state_survives_a_restart() {
    let db = database();
    let first = {
        let mut e = open(&db);
        e.query("alice", COUNT, 0.05).unwrap()
    };
    let mut e = open(&db);
    let (_, bal) = e.balance("alice").unwrap().unwrap();
    assert!((bal.spent - 0.05).abs() < 1e-12);
    // The cache came back too: free, and the very same answer.
    let again = e.query("alice", COUNT, 0.05).unwrap();
    assert_eq!(again.status, "cache_hit");
    assert_eq!(again.rows, first.rows);
    assert_eq!(e.audit_rows("alice").unwrap().len(), 2);
    assert!(e.verify_ledger("alice").unwrap().unwrap().ok);
    // Grants and roles too.
    assert!(e
        .grants()
        .allows(&["dp_analyst".to_string()], "visits", "total_charge"));
    assert_eq!(e.principal("erin").unwrap().roles, ["auditor"]);
}

#[test]
fn a_tampered_ledger_on_disk_stops_the_engine_from_starting() {
    let db = database();
    {
        let mut e = open(&db);
        e.query("alice", COUNT, 0.05).unwrap();
        e.query("alice", "SELECT SUM(total_charge) FROM visits", 0.05)
            .unwrap();
    }
    // An attacker with disk access makes the first charge look free.
    let mut rows = read_sys_rows(&db, "sys_budget_ledger").unwrap();
    assert!(rows.len() >= 4);
    rows[0][4] = Value::F64(0.0001); // rho column
    write_sys_rows(&db, "sys_budget_ledger", &rows).unwrap();
    let err = Engine::open(&db, GOLDEN_SECRET).err().unwrap();
    assert!(err.to_string().contains("tampering"), "{err}");
}

#[test]
fn explain_describes_the_plan_without_spending() {
    let db = database();
    let e = open(&db);
    let text = e
        .explain(
            "alice",
            "SELECT SUM(total_charge) FROM visits WHERE dept_id = 2 GROUP BY admission_type",
        )
        .unwrap();
    let j = json::parse(&text).unwrap();
    let ops: Vec<&str> = j
        .get("physical")
        .unwrap()
        .as_array()
        .unwrap()
        .iter()
        .map(|o| o.as_str().unwrap())
        .collect();
    assert_eq!(
        ops,
        [
            "Scan",
            "Filter",
            "ContributionBound",
            "Clamp",
            "PartitionSelect",
            "DPAggregate"
        ]
    );
    assert_eq!(j.get("mechanism").unwrap().as_str(), Some("gaussian"));
    assert_eq!(j.get("cached").unwrap(), &Json::Bool(false));
    let (_, bal) = e.balance("alice").unwrap().unwrap();
    assert_eq!(bal.spent, 0.0);
    assert!(e.audit_rows("alice").unwrap().is_empty());
    // RBAC applies to EXPLAIN too.
    assert!(e.explain("carol", COUNT).is_err());
}

#[test]
fn analysts_see_only_their_own_budget_and_audit_rows() {
    let db = database();
    let mut e = open(&db);
    e.query("alice", COUNT, 0.05).unwrap();
    e.query("frank", COUNT, 0.05).unwrap();
    assert_eq!(e.balances("alice").unwrap().len(), 1);
    assert_eq!(e.balances("erin").unwrap().len(), 3);
    assert_eq!(e.audit_rows("alice").unwrap().len(), 1);
    assert_eq!(e.audit_rows("erin").unwrap().len(), 2);
}

#[test]
fn the_server_secret_must_be_long_enough() {
    let db = database();
    assert!(Engine::open(&db, b"short").is_err());
}

#[test]
fn a_reservation_left_dangling_by_a_crash_is_released_on_startup() {
    use privguard_privacy::ledger::{BudgetLedger, ChargeMeta, LedgerEntry};
    let db = database();
    {
        let mut e = open(&db);
        e.query("alice", COUNT, 0.05).unwrap();
    }
    // Simulate a crash after `reserve` but before `commit`/`abort`.
    let entries: Vec<LedgerEntry> = read_sys_rows(&db, "sys_budget_ledger")
        .unwrap()
        .iter()
        .map(|r| LedgerEntry::from_row(r).unwrap())
        .collect();
    let accounts: Vec<(u64, f64)> = read_sys_rows(&db, "sys_budget_account")
        .unwrap()
        .iter()
        .map(|r| match (&r[0], &r[2]) {
            (Value::I64(a), Value::F64(t)) => (*a as u64, *t),
            _ => panic!(),
        })
        .collect();
    let mut ledger = BudgetLedger::restore(&accounts, entries).unwrap();
    ledger.reserve(1, 9999, 0.1, ChargeMeta::default()).unwrap();
    let mut rows = Vec::new();
    for (a, _) in ledger.accounts() {
        rows.extend(ledger.entries(a).unwrap().iter().map(LedgerEntry::to_row));
    }
    write_sys_rows(&db, "sys_budget_ledger", &rows).unwrap();

    let e = open(&db);
    let (_, bal) = e.balance("alice").unwrap().unwrap();
    assert_eq!(bal.reserved, 0.0, "the hold was released");
    assert!((bal.spent - 0.05).abs() < 1e-12);
    let v = e.verify_ledger("alice").unwrap().unwrap();
    assert!(v.ok);
    assert_eq!(
        v.entries, 4,
        "reserve, commit, dangling reserve, recovery abort"
    );
}

#[test]
fn only_a_scalar_count_is_declared_an_integer() {
    use privguard_core::ColumnType;
    let db = database();
    let mut e = open(&db);
    let scalar = e.query("alice", COUNT, 0.5).unwrap();
    assert_eq!(scalar.columns, [("value".to_string(), ColumnType::I64)]);
    let grouped = e
        .query(
            "alice",
            "SELECT COUNT(*) FROM visits GROUP BY admission_type",
            0.5,
        )
        .unwrap();
    assert_eq!(grouped.columns[1], ("value".to_string(), ColumnType::F64));
    // Every released cell matches its declared type, or a client would fail to decode the row.
    for row in &grouped.rows {
        assert!(matches!(row[1], Value::F64(_)), "{row:?}");
    }
}

#[test]
fn the_policy_grid_lists_the_fact_table_and_is_dba_or_auditor_only() {
    use privguard_catalog::AccessMode;
    let db = database();
    let e = open(&db);
    let cols = e.policy_columns("dave").unwrap();
    assert_eq!(cols.len(), 8);
    let charge = cols.iter().find(|c| c.column == "total_charge").unwrap();
    assert_eq!(charge.access, AccessMode::AggregateOnly);
    assert_eq!(charge.clamp, Some((0.0, 1000.0)));
    assert_eq!(charge.mechanism.as_deref(), Some("laplace"));
    assert!(charge.filterable && !charge.groupable);
    let pid = cols.iter().find(|c| c.column == "patient_id").unwrap();
    assert_eq!(pid.access, AccessMode::Denied);
    assert!(e.policy_columns("erin").is_ok(), "auditors may read");
    assert_eq!(
        e.policy_columns("alice").unwrap_err().sqlstate().code(),
        "42501"
    );
    assert_eq!(e.roles(), ["auditor", "clinician", "dba", "dp_analyst"]);
    assert!(e
        .grant_rows("dave")
        .unwrap()
        .iter()
        .any(|g| g.0 == "dp_analyst" && g.2 == "dept_id"));
}

#[test]
fn only_a_dba_can_change_grants_and_a_change_takes_effect_at_once() {
    let db = database();
    let mut e = open(&db);
    let q = "SELECT COUNT(*) FROM visits WHERE dept_id = 1";
    assert!(e.query("alice", q, 0.05).is_ok());

    assert_eq!(
        e.revoke_as("alice", "dp_analyst", "visits", "dept_id")
            .unwrap_err()
            .sqlstate()
            .code(),
        "42501"
    );
    assert_eq!(
        e.grant_as("erin", "clinician", "visits", "dept_id")
            .unwrap_err()
            .sqlstate()
            .code(),
        "42501"
    );

    assert!(e
        .revoke_as("dave", "dp_analyst", "visits", "dept_id")
        .unwrap());
    assert!(!e
        .revoke_as("dave", "dp_analyst", "visits", "dept_id")
        .unwrap());
    // A different spelling of the same question, so the cache cannot answer it.
    let denied = e.query(
        "alice",
        "SELECT COUNT(*) FROM visits WHERE dept_id = 2",
        0.05,
    );
    assert_eq!(denied.unwrap_err().sqlstate().code(), "42501");

    e.grant_as("dave", "dp_analyst", "visits", "dept_id")
        .unwrap();
    assert!(e
        .query(
            "alice",
            "SELECT COUNT(*) FROM visits WHERE dept_id = 2",
            0.05
        )
        .is_ok());

    assert_eq!(
        e.grant_as("dave", "nobody", "visits", "dept_id")
            .unwrap_err()
            .sqlstate()
            .code(),
        "42P01"
    );
    assert!(e
        .grant_as("dave", "dp_analyst", "visits", "no_such_column")
        .is_err());
    // Changes are durable.
    drop(e);
    let e = open(&db);
    assert!(e
        .grants()
        .allows(&["dp_analyst".to_string()], "visits", "dept_id"));
}
