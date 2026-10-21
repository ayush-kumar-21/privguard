//! Bootstrap-table rewrites are atomic (Phase 5 crash behaviour): a failed or interrupted rewrite
//! must leave the previous contents intact. Before the fix, `write_sys_rows` truncated the file
//! first, so any failure after that point emptied the budget ledger.

use privguard_catalog::{read_sys_rows, write_sys_rows};
use privguard_core::Value;
use std::path::PathBuf;

fn dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("privguard-atomic-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn role(id: i64, name: &str) -> Vec<Value> {
    vec![Value::I64(id), Value::Text(name.into())]
}

#[test]
fn a_rewrite_replaces_the_table_and_leaves_no_temp_file() {
    let d = dir("replace");
    write_sys_rows(&d, "sys_role", &[role(1, "a"), role(2, "b")]).unwrap();
    write_sys_rows(&d, "sys_role", &[role(3, "c")]).unwrap();
    assert_eq!(read_sys_rows(&d, "sys_role").unwrap(), vec![role(3, "c")]);
    assert!(!d.join("sys_role.heap.tmp").exists());
}

#[test]
fn a_rewrite_that_fails_midway_keeps_the_old_contents() {
    let d = dir("fail");
    write_sys_rows(&d, "sys_role", &[role(1, "a"), role(2, "b")]).unwrap();
    // The second row has the wrong column types, so encoding fails after the first was written.
    let bad = vec![Value::Text("not an id".into()), Value::I64(7)];
    assert!(write_sys_rows(&d, "sys_role", &[role(9, "new"), bad]).is_err());
    assert_eq!(
        read_sys_rows(&d, "sys_role").unwrap(),
        vec![role(1, "a"), role(2, "b")],
        "a failed rewrite must not truncate the table"
    );
    assert!(!d.join("sys_role.heap.tmp").exists());
}

#[test]
fn a_torn_temp_file_left_by_a_crash_is_ignored_and_replaced() {
    let d = dir("torn");
    write_sys_rows(&d, "sys_role", &[role(1, "a")]).unwrap();
    // A crash mid-rewrite leaves a partial temp file; the real table is untouched.
    std::fs::write(d.join("sys_role.heap.tmp"), vec![0xAB; 100]).unwrap();
    assert_eq!(read_sys_rows(&d, "sys_role").unwrap(), vec![role(1, "a")]);
    // The next rewrite starts over from the stale temp file.
    write_sys_rows(&d, "sys_role", &[role(2, "b")]).unwrap();
    assert_eq!(read_sys_rows(&d, "sys_role").unwrap(), vec![role(2, "b")]);
    assert!(!d.join("sys_role.heap.tmp").exists());
}
