//! The PrivGuard engine daemon.
//!
//! Wires the storage, catalog, planning, privacy and wire-protocol crates into
//! one process listening on the Postgres wire protocol.
//!
//! Until Phase 4.3 lands the listener, running this reports what is built and
//! what is not, so `run.bat engine` has something honest to show.

/// `privguardd load <csv_dir> <db_dir>`: load the SafeHealth CSVs into a new catalog directory.
fn load(csv_dir: &str, db_dir: &str) -> Result<(), privguard_core::Error> {
    let (_, report) = privguard_catalog::load_safehealth(
        std::path::Path::new(csv_dir),
        std::path::Path::new(db_dir),
    )?;
    for (table, rows) in report.rows {
        println!("  {table:<12} {rows:>6} rows");
    }
    println!("Catalog written to {db_dir}");
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("load") {
        let [_, csv, db] = args.as_slice() else {
            eprintln!("usage: privguardd load <csv_dir> <db_dir>");
            std::process::exit(2);
        };
        if let Err(e) = load(csv, db) {
            eprintln!("{e}");
            std::process::exit(1);
        }
        return;
    }

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

    // A worked example of the accounting, so the numbers in SPEC.md §4.1 can be
    // eyeballed without running the test suite.
    let (rho, delta) = (privguard_privacy::defaults::RHO, 1e-6);
    let sensitivity = privguard_privacy::defaults::MAX_ROWS_PER_UNIT as f64;
    println!(
        "  COUNT(*) at rho={rho}: laplace b={:.4}, reported eps={:.4} at delta={delta:e}",
        privguard_privacy::accounting::laplace_scale_from_rho(sensitivity, rho),
        privguard_privacy::accounting::eps_from_rho(rho, delta),
    );
    println!();
    println!("Not yet listening: the pgwire server lands in Phase 4.3.");
    println!("See IMPLEMENTATION_PLAN.md for the current resume point.");
}
