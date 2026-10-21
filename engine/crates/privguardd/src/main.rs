//! The PrivGuard engine daemon.
//!
//! ```text
//! privguardd load <csv_dir> <db_dir>     load the SafeHealth CSVs into a new database
//! privguardd seed-demo <db_dir>          add the demo principals, budgets and column grants
//! privguardd serve <db_dir> [addr]       listen on the Postgres wire protocol (default 127.0.0.1:5433)
//! privguardd                             print what is built
//! ```
//!
//! `serve` reads two secrets from the environment and refuses to start without them:
//! `PRIVGUARD_SERVER_SECRET` keys the noise (`SPEC.md` §4.4) and `PRIVGUARD_ENGINE_SECRET` is the
//! password the gateway presents (`SPEC.md` §10 A2).

use privguard_core::Error;
use privguardd::engine::{seed_demo, Engine};
use std::path::Path;
use std::sync::{Arc, Mutex};

const DEFAULT_ADDR: &str = "127.0.0.1:5433";
const MIN_SECRET_LEN: usize = 16;

fn secret(var: &str) -> Result<String, Error> {
    match std::env::var(var) {
        Ok(s) if s.len() >= MIN_SECRET_LEN => Ok(s),
        Ok(_) => Err(Error::policy(format!(
            "{var} must be at least {MIN_SECRET_LEN} characters"
        ))),
        Err(_) => Err(Error::policy(format!("{var} is not set"))),
    }
}

/// `privguardd load <csv_dir> <db_dir>`
fn load(csv_dir: &str, db_dir: &str) -> Result<(), Error> {
    // With PRIVGUARD_COLUMN_KEY set, patients.mrn is stored AES-256-GCM encrypted.
    let keyring = privguard_crypto::Keyring::from_env()?;
    let encrypted = keyring.is_some();
    let (_, report) =
        privguard_catalog::load_safehealth_with(Path::new(csv_dir), Path::new(db_dir), keyring)?;
    if encrypted {
        println!("  patients.mrn stored encrypted (AES-256-GCM)");
    }
    for (table, rows) in report.rows {
        println!("  {table:<12} {rows:>6} rows");
    }
    println!("Database written to {db_dir}");
    Ok(())
}

/// `privguardd seed-demo <db_dir>`
fn seed(db_dir: &str) -> Result<(), Error> {
    let mut engine = Engine::open(
        Path::new(db_dir),
        secret("PRIVGUARD_SERVER_SECRET")?.as_bytes(),
    )?;
    seed_demo(&mut engine)?;
    println!("Seeded alice, frank, erin, carol and dave in {db_dir}");
    Ok(())
}

/// `privguardd serve <db_dir> [addr]`
fn serve(db_dir: &str, addr: &str) -> Result<(), Error> {
    let noise = secret("PRIVGUARD_SERVER_SECRET")?;
    let service = secret("PRIVGUARD_ENGINE_SECRET")?;
    let engine = Engine::open(Path::new(db_dir), noise.as_bytes())?;

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|e| Error::engine(format!("cannot start the runtime: {e}")))?;
    runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .map_err(|e| Error::engine(format!("cannot listen on {addr}: {e}")))?;
        println!("PrivGuard engine listening on {addr} (database {db_dir})");
        privguardd::server::serve(listener, Arc::new(Mutex::new(engine)), Arc::new(service))
            .await
            .map_err(|e| Error::engine(format!("server stopped: {e}")))
    })
}

fn banner() {
    println!("PrivGuard engine v{}", env!("CARGO_PKG_VERSION"));
    println!();
    println!("  page size          {} B", privguard_core::PAGE_SIZE);
    println!("  default rho        {}", privguard_privacy::defaults::RHO);
    println!(
        "  contribution bound C={} rows, G={} groups",
        privguard_privacy::defaults::MAX_ROWS_PER_UNIT,
        privguard_privacy::defaults::MAX_GROUPS_PER_UNIT
    );
    println!(
        "  catalog tables     {}",
        privguard_catalog::BOOTSTRAP_TABLES.len()
    );
    println!(
        "  wire protocol      v3 ({})",
        privguard_wire::PROTOCOL_VERSION
    );
    println!();
    println!("Commands: load <csv_dir> <db_dir> | seed-demo <db_dir> | serve <db_dir> [addr]");
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.as_slice() {
        [] => {
            banner();
            Ok(())
        }
        ["load", csv, db] => load(csv, db),
        ["seed-demo", db] => seed(db),
        ["serve", db] => serve(db, DEFAULT_ADDR),
        ["serve", db, addr] => serve(db, addr),
        _ => {
            eprintln!("usage: privguardd load <csv_dir> <db_dir> | seed-demo <db_dir> | serve <db_dir> [addr]");
            std::process::exit(2);
        }
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
