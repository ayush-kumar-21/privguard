//! The privacy rewriter: query fingerprint, noise cache, and predicate-overlap detection.
//! `IMPLEMENTATION_PLAN.md` 3.5 and 3.6; `SPEC.md` §4.6.
//!
//! The cache is what defeats repeat-and-average. The same `(fingerprint, principal)` always gets
//! the *stored* answer and pays nothing, so asking again cannot buy a second independent noise
//! draw. Overlap detection closes the other route to the same attack: asking a differently
//! spelled question about (almost) the same rows, e.g. `Q(D)` then `Q(D minus one patient)`, or
//! a tautology appended to the `WHERE` clause.

use privguard_core::json::{self, Json};
use privguard_core::{Error, Result, Row, Value};
use privguard_privacy::{sha256, QueryFingerprint};
use privguard_sql::BoundQuery;
use std::collections::{BTreeMap, BTreeSet};

/// Two queries whose matched row sets have at least this Jaccard similarity count as overlapping.
pub const OVERLAP_THRESHOLD: f64 = 0.8;

/// `SHA-256` of the canonical form (`SPEC.md` §4.6). Predicate order does not matter.
pub fn fingerprint(q: &BoundQuery) -> QueryFingerprint {
    QueryFingerprint::from_digest(sha256(q.canonical().as_bytes()))
}

/// Sorted rendering of just the predicates. Two queries with the same key ask about the same rows
/// by definition, so they are not an overlap attack even when their aggregates differ.
pub fn filter_key(q: &BoundQuery) -> String {
    let mut parts: Vec<String> = q
        .filters
        .iter()
        .map(|f| format!("{}{}{:?}", f.column, f.op.canonical(), f.value))
        .collect();
    parts.sort();
    parts.join("&")
}

// ---------------------------------------------------------------- noise cache

/// What was released and how it was calibrated; replayed verbatim on a cache hit.
#[derive(Debug, Clone, PartialEq)]
pub struct CachedAnswer {
    pub rows: Vec<Row>,
    pub mechanism: String,
    pub sensitivity: f64,
    pub scale: f64,
    pub rho: f64,
    pub epsilon: f64,
    pub suppressed_groups: u64,
}

#[derive(Debug, Clone, Default)]
pub struct NoiseCache {
    entries: BTreeMap<(String, u64), CachedAnswer>,
}

impl NoiseCache {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn get(&self, fp: &QueryFingerprint, principal: u64) -> Option<&CachedAnswer> {
        self.entries.get(&(fp.to_hex(), principal))
    }

    /// Remember the first answer. A second insert for the same key is ignored: the stored
    /// answer is the only one ever released.
    pub fn insert(&mut self, fp: &QueryFingerprint, principal: u64, answer: CachedAnswer) {
        self.entries
            .entry((fp.to_hex(), principal))
            .or_insert(answer);
    }

    /// Rows of `sys_noise_cache`: `(fingerprint, principal_id, result, rho)`.
    pub fn to_rows(&self) -> Vec<Row> {
        self.entries
            .iter()
            .map(|((fp, principal), a)| {
                let rows = a
                    .rows
                    .iter()
                    .map(|r| Json::Arr(r.iter().map(Json::from_value).collect()))
                    .collect();
                let body = Json::Obj(vec![
                    ("rows".into(), Json::Arr(rows)),
                    ("mechanism".into(), Json::str(&a.mechanism)),
                    ("sensitivity".into(), Json::float(a.sensitivity)),
                    ("scale".into(), Json::float(a.scale)),
                    ("epsilon".into(), Json::float(a.epsilon)),
                    ("suppressed".into(), Json::int(a.suppressed_groups as i64)),
                ]);
                vec![
                    Value::Text(fp.clone()),
                    Value::I64(*principal as i64),
                    Value::Text(body.to_json_string()),
                    Value::F64(a.rho),
                ]
            })
            .collect()
    }

    pub fn from_rows(rows: &[Row]) -> Result<Self> {
        let bad = || Error::engine("malformed sys_noise_cache row");
        let mut cache = NoiseCache::new();
        for r in rows {
            let (
                Some(Value::Text(fp)),
                Some(Value::I64(p)),
                Some(Value::Text(body)),
                Some(Value::F64(rho)),
            ) = (r.first(), r.get(1), r.get(2), r.get(3))
            else {
                return Err(bad());
            };
            let j = json::parse(body)?;
            let get = |k: &str| j.get(k).ok_or_else(bad);
            let rows = get("rows")?
                .as_array()
                .ok_or_else(bad)?
                .iter()
                .map(|row| {
                    row.as_array()
                        .ok_or_else(bad)?
                        .iter()
                        .map(|c| c.to_value().ok_or_else(bad))
                        .collect::<Result<Row>>()
                })
                .collect::<Result<Vec<_>>>()?;
            let answer = CachedAnswer {
                rows,
                mechanism: get("mechanism")?.as_str().ok_or_else(bad)?.to_string(),
                sensitivity: get("sensitivity")?.as_f64().ok_or_else(bad)?,
                scale: get("scale")?.as_f64().ok_or_else(bad)?,
                rho: *rho,
                epsilon: get("epsilon")?.as_f64().ok_or_else(bad)?,
                suppressed_groups: get("suppressed")?.as_i64().ok_or_else(bad)? as u64,
            };
            cache.entries.insert(
                (QueryFingerprint::from_hex(fp)?.to_hex(), *p as u64),
                answer,
            );
        }
        Ok(cache)
    }
}

// ------------------------------------------------------------ overlap detection

/// Jaccard similarity of two row-id sets; `0` when either is empty.
pub fn jaccard(a: &BTreeSet<i64>, b: &BTreeSet<i64>) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let inter = a.intersection(b).count() as f64;
    inter / (a.len() as f64 + b.len() as f64 - inter)
}

struct Seen {
    filter_key: String,
    rows: BTreeSet<i64>,
}

/// Remembers, per principal, which rows each answered query matched.
#[derive(Default)]
pub struct OverlapDetector {
    history: BTreeMap<u64, Vec<Seen>>,
}

impl OverlapDetector {
    pub fn new() -> Self {
        Self::default()
    }

    /// Refuse `rows` if they nearly coincide with an earlier answered query of the same principal
    /// that asked about them differently (a different predicate set).
    pub fn check(&self, principal: u64, filter_key: &str, rows: &BTreeSet<i64>) -> Result<()> {
        for seen in self.history.get(&principal).into_iter().flatten() {
            if seen.filter_key != filter_key && jaccard(&seen.rows, rows) >= OVERLAP_THRESHOLD {
                return Err(Error::policy(format!(
                    "query overlaps an earlier one by {:.0}% of its rows; \
                     near-duplicate questions would let noise be averaged away",
                    100.0 * jaccard(&seen.rows, rows)
                )));
            }
        }
        Ok(())
    }

    pub fn record(&mut self, principal: u64, filter_key: String, rows: BTreeSet<i64>) {
        let seen = self.history.entry(principal).or_default();
        if !seen.iter().any(|s| s.filter_key == filter_key) {
            seen.push(Seen { filter_key, rows });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use privguard_sql::parse;

    fn set(r: std::ops::Range<i64>) -> BTreeSet<i64> {
        r.collect()
    }

    #[test]
    fn predicate_order_and_case_do_not_change_the_fingerprint() {
        let a =
            parse("SELECT COUNT(*) FROM visits WHERE dept_id = 1 AND total_charge > 5").unwrap();
        let b =
            parse("select count(*) from VISITS where total_charge > 5 and dept_id = 1").unwrap();
        assert_eq!(fingerprint(&a), fingerprint(&b));
        let c =
            parse("SELECT COUNT(*) FROM visits WHERE dept_id = 2 AND total_charge > 5").unwrap();
        assert_ne!(fingerprint(&a), fingerprint(&c));
    }

    #[test]
    fn the_fingerprint_matches_a_golden_oracle_value() {
        let q = parse("SELECT COUNT(*) FROM visits").unwrap();
        assert_eq!(
            fingerprint(&q).to_hex(),
            "edc6f9946aed8757f617dac4a6ea3c1120f72cbaac6267577007b2db661238a5"
        );
    }

    #[test]
    fn filter_keys_ignore_order_but_not_content() {
        let a =
            parse("SELECT COUNT(*) FROM visits WHERE dept_id = 1 AND total_charge > 5").unwrap();
        let b =
            parse("SELECT SUM(total_charge) FROM visits WHERE total_charge > 5 AND dept_id = 1")
                .unwrap();
        assert_eq!(filter_key(&a), filter_key(&b));
        let c =
            parse("SELECT COUNT(*) FROM visits WHERE dept_id = 1 AND total_charge > 6").unwrap();
        assert_ne!(filter_key(&a), filter_key(&c));
    }

    fn answer(v: i64) -> CachedAnswer {
        CachedAnswer {
            rows: vec![
                vec![Value::Text("a".into()), Value::I64(v)],
                vec![Value::Null, Value::F64(2.0)],
            ],
            mechanism: "gaussian".into(),
            sensitivity: 8.66,
            scale: 9.0,
            rho: 0.05,
            epsilon: 1.7,
            suppressed_groups: 2,
        }
    }

    #[test]
    fn the_cache_returns_the_first_answer_only() {
        let fp = fingerprint(&parse("SELECT COUNT(*) FROM visits").unwrap());
        let mut cache = NoiseCache::new();
        assert!(cache.get(&fp, 1).is_none());
        cache.insert(&fp, 1, answer(10));
        cache.insert(&fp, 1, answer(99));
        assert_eq!(cache.get(&fp, 1).unwrap().rows[0][1], Value::I64(10));
        assert_eq!(cache.len(), 1);
    }

    #[test]
    fn the_cache_is_per_principal() {
        let fp = fingerprint(&parse("SELECT COUNT(*) FROM visits").unwrap());
        let mut cache = NoiseCache::new();
        cache.insert(&fp, 1, answer(10));
        assert!(cache.get(&fp, 2).is_none());
    }

    #[test]
    fn cache_rows_round_trip_with_types_intact() {
        let fp = fingerprint(&parse("SELECT COUNT(*) FROM visits").unwrap());
        let mut cache = NoiseCache::new();
        cache.insert(&fp, 7, answer(10));
        let restored = NoiseCache::from_rows(&cache.to_rows()).unwrap();
        assert_eq!(restored.get(&fp, 7), cache.get(&fp, 7));
        assert!(NoiseCache::from_rows(&[vec![Value::Null]]).is_err());
    }

    #[test]
    fn jaccard_is_intersection_over_union() {
        assert_eq!(jaccard(&set(0..10), &set(0..10)), 1.0);
        assert!((jaccard(&set(0..10), &set(5..15)) - 5.0 / 15.0).abs() < 1e-12);
        assert_eq!(jaccard(&set(0..5), &set(5..10)), 0.0);
        assert_eq!(jaccard(&set(0..0), &set(0..5)), 0.0);
        assert!((jaccard(&set(0..600), &set(1..600)) - 599.0 / 600.0).abs() < 1e-12);
    }

    #[test]
    fn a_differencing_pair_is_refused() {
        let mut d = OverlapDetector::new();
        d.record(1, "all".into(), set(0..600));
        // Q(D minus one row): 599/600 similar, different predicates.
        let err = d.check(1, "all-but-one", &set(1..600)).unwrap_err();
        assert_eq!(err.sqlstate().code(), "42501");
        assert!(err.to_string().contains("overlaps"));
    }

    #[test]
    fn a_tautological_rewrite_is_refused_but_the_identical_question_is_not() {
        let mut d = OverlapDetector::new();
        d.record(1, "total_charge<=1000001".into(), set(0..100));
        assert!(d.check(1, "total_charge<=1000002", &set(0..100)).is_err());
        // Same predicates, different aggregate: allowed (it is charged, not averaged).
        d.check(1, "total_charge<=1000001", &set(0..100)).unwrap();
    }

    #[test]
    fn distinct_populations_and_other_principals_are_unaffected() {
        let mut d = OverlapDetector::new();
        d.record(1, "a".into(), set(0..100));
        d.check(1, "b", &set(100..200)).unwrap();
        d.check(1, "b", &set(50..150)).unwrap(); // 50/150 similar
        d.check(2, "b", &set(0..100)).unwrap();
    }
}
