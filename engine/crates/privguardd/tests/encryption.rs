//! Column encryption end to end: loader -> heap file -> executor scan (`SPEC.md` §5.5).

mod common;
use common::*;
use privguard_catalog::{load_safehealth_with, Catalog};
use privguard_core::{RowSource, Value};
use privguard_crypto::Keyring;
use privguard_plan::exec::HeapScan;
use privguard_storage::ValueCipher;
use std::path::PathBuf;

const KEY: &str = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff";

fn csv_with_patients() -> PathBuf {
    let root = temp_dir("enc");
    let csv = root.join("csv");
    std::fs::create_dir_all(&csv).unwrap();
    write_csvs(&csv);
    std::fs::write(
        csv.join("patients.csv"),
        "patient_id,region_id,mrn,birth_year,sex,insurance_type,enrolled_at\n\
         1,1,MRN000001,1980,F,public,2021-01-01\n\
         2,1,MRN000002,1975,M,private,2022-02-02\n",
    )
    .unwrap();
    root
}

fn scan_patients(cat: &Catalog) -> privguard_core::Result<Vec<Vec<Value>>> {
    let types = cat.table("patients").unwrap().column_types();
    let cipher = cat
        .table_cipher("patients")?
        .map(|c| Box::new(c) as Box<dyn ValueCipher>);
    HeapScan::with_cipher(&cat.open_heap("patients")?, types, cipher)?.collect_rows()
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|w| w == needle)
}

#[test]
fn an_encrypted_column_is_ciphertext_on_disk_and_readable_with_the_key() {
    let root = csv_with_patients();
    let key = Keyring::from_hex(KEY).unwrap();
    let (cat, _) = load_safehealth_with(&root.join("csv"), &root.join("db"), Some(key)).unwrap();

    let mrn = cat.table("patients").unwrap().column("mrn").unwrap().1;
    assert!(mrn.encrypted);
    assert!(
        !cat.table("patients")
            .unwrap()
            .column("sex")
            .unwrap()
            .1
            .encrypted
    );

    // The page bytes never hold the plaintext...
    let raw = std::fs::read(cat.heap_path("patients")).unwrap();
    assert!(!contains(&raw, b"MRN000001"), "plaintext on disk");
    assert!(
        contains(&raw, b"public"),
        "unencrypted columns are stored as usual"
    );

    // ...and the executor, given the key, reads it back.
    let rows = scan_patients(&cat).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0][2], Value::Text("MRN000001".into()));
    assert_eq!(rows[1][2], Value::Text("MRN000002".into()));
}

#[test]
fn the_key_is_not_in_the_catalog_so_a_reopened_catalog_refuses_without_it() {
    let root = csv_with_patients();
    let key = Keyring::from_hex(KEY).unwrap();
    load_safehealth_with(&root.join("csv"), &root.join("db"), Some(key)).unwrap();

    for entry in std::fs::read_dir(root.join("db")).unwrap() {
        let bytes = std::fs::read(entry.unwrap().path()).unwrap();
        assert!(
            !contains(&bytes, &hex_bytes(KEY)),
            "key material found on disk"
        );
    }

    let mut cat = Catalog::open(&root.join("db")).unwrap();
    // The flag survives the restart, but without a key the table cannot be read.
    let err = scan_patients(&cat).err().unwrap();
    assert_eq!(err.sqlstate().code(), "42501");
    assert!(err.to_string().contains("PRIVGUARD_COLUMN_KEY"), "{err}");

    // A wrong key fails authentication instead of returning garbage.
    cat.set_keyring(Some(Keyring::from_bytes([7u8; 32])));
    let err = scan_patients(&cat).err().unwrap();
    assert!(err.to_string().contains("failed authentication"), "{err}");

    // The right key works again.
    cat.set_keyring(Some(Keyring::from_hex(KEY).unwrap()));
    assert_eq!(scan_patients(&cat).unwrap().len(), 2);
}

fn hex_bytes(hex: &str) -> Vec<u8> {
    (0..hex.len() / 2)
        .map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).unwrap())
        .collect()
}

#[test]
fn without_a_key_nothing_is_encrypted_and_the_catalog_says_so() {
    let root = csv_with_patients();
    let (cat, _) = load_safehealth_with(&root.join("csv"), &root.join("db"), None).unwrap();
    assert!(
        !cat.table("patients")
            .unwrap()
            .column("mrn")
            .unwrap()
            .1
            .encrypted
    );
    assert!(contains(
        &std::fs::read(cat.heap_path("patients")).unwrap(),
        b"MRN000001"
    ));
    assert_eq!(scan_patients(&cat).unwrap().len(), 2);
}

#[test]
fn private_queries_on_other_tables_do_not_need_the_key() {
    // The fact table has no encrypted columns, so the engine answers without a key loaded.
    let root = csv_with_patients();
    let key = Keyring::from_hex(KEY).unwrap();
    let db = root.join("db");
    load_safehealth_with(&root.join("csv"), &db, Some(key)).unwrap();
    let cat = Catalog::open(&db).unwrap();
    assert!(cat.table_cipher("visits").unwrap().is_none());
    assert_eq!(
        cat.open_heap("visits").unwrap().row_count().unwrap() as usize,
        fixture_visits()
    );
}

fn fixture_visits() -> usize {
    fixture("dataset.json").as_array().unwrap().len()
}
