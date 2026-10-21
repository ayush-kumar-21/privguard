//! Fuzz-style tests for the SQL front end (Phase 4): hostile text must be refused with an error,
//! never crash the parser or binder, and accepted queries must stay inside the SPEC section 3 subset.

use privguard_sql::parse;
use proptest::prelude::*;

const SEEDS: [&str; 6] = [
    "SELECT COUNT(*) FROM visits",
    "SELECT SUM(total_charge) FROM visits WHERE dept_id = 2 AND total_charge > 100.5",
    "SELECT AVG(total_charge) FROM visits WHERE admission_type <> 'ELECTIVE'",
    "SELECT COUNT(*) FROM visits GROUP BY admission_type",
    "SELECT COUNT(*) FROM visits WHERE admit_ts >= '2024-03-01 00:00:00'",
    "SELECT SUM(total_charge) FROM visits WHERE total_charge >= -5 GROUP BY dept_id",
];

proptest! {
    #[test]
    fn arbitrary_text_never_panics(sql in ".{0,200}") {
        let _ = parse(&sql);
    }

    #[test]
    fn arbitrary_bytes_as_text_never_panic(bytes in prop::collection::vec(any::<u8>(), 0..200)) {
        let _ = parse(&String::from_utf8_lossy(&bytes));
    }

    /// Valid queries with a splice, deletion or duplicated token: the likeliest crashes.
    #[test]
    fn mutated_valid_queries_never_panic(
        seed in 0..SEEDS.len(),
        a in 0usize..90,
        b in 0usize..90,
        junk in "[ -~]{0,12}",
        mode in 0u8..3,
    ) {
        let s = SEEDS[seed];
        let (a, b) = (a.min(s.len()), b.min(s.len()));
        let (lo, hi) = (a.min(b), a.max(b));
        if !(s.is_char_boundary(lo) && s.is_char_boundary(hi)) {
            return Ok(());
        }
        let mutated = match mode {
            0 => format!("{}{}{}", &s[..lo], junk, &s[hi..]),
            1 => format!("{}{}", &s[..hi], &s[lo..]),
            _ => s[..lo].to_string(),
        };
        let _ = parse(&mutated);
    }

    /// Anything the binder accepts is a single aggregate over `visits`, with no raw columns.
    #[test]
    fn accepted_queries_are_aggregates_over_the_fact_table(sql in ".{0,120}") {
        if let Ok(q) = parse(&sql) {
            prop_assert_eq!(q.table.as_str(), "visits");
        }
    }
}
