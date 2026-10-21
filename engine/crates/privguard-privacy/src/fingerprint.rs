//! Query fingerprint. Phase 0 freeze. `SPEC.md` §4.6.
//!
//! The fingerprint is `SHA-256` over a canonical string:
//!
//! ```text
//! agg | column-or-* | table | (col,op,value)* sorted | GROUP=<col-or-empty>
//! ```
//!
//! It must match `QuerySpec.canonical()` in `oracle/privguard_oracle/spec.py`
//! byte for byte, because two spellings of one question have to share a
//! fingerprint or the noise cache leaks (an analyst could average two draws).
//!
//! This module defines the canonical string and the 32-byte fingerprint type.
//! Hashing the string needs SHA-256, which arrives with the keyed noise stream in
//! Phase 1 task 3.2 (which needs it too); `QueryFingerprint::from_digest` is the
//! seam. Until then the canonical string is the interop surface.

use privguard_core::{Error, Result};
use std::fmt;

/// A literal in a `WHERE` predicate, rendered the way Python's `repr` renders it,
/// since the oracle hashes `repr(value)`.
#[derive(Debug, Clone, PartialEq)]
pub enum Literal {
    Int(i64),
    Float(f64),
    Text(String),
    Bool(bool),
}

impl Literal {
    /// Python `repr(value)`.
    fn py_repr(&self) -> Result<String> {
        Ok(match self {
            Literal::Int(v) => v.to_string(),
            Literal::Bool(true) => "True".to_string(),
            Literal::Bool(false) => "False".to_string(),
            Literal::Float(v) => {
                let s = format!("{v:?}");
                // Rust and Python spell exponents differently ("1e16" vs
                // "1e+16"), and NaN/inf differ too. Refuse rather than
                // fingerprint differently from the oracle.
                if !v.is_finite() || s.contains(['e', 'E']) {
                    return Err(Error::parse(format!(
                        "float literal {s} cannot be fingerprinted"
                    )));
                }
                s
            }
            Literal::Text(t) => py_str_repr(t)?,
        })
    }

    /// Python `str(value)`, used only as the third sort key.
    fn py_str(&self) -> Result<String> {
        match self {
            Literal::Text(t) => Ok(t.clone()),
            other => other.py_repr(),
        }
    }
}

/// Python's `repr` for a `str`: single quotes unless the text contains `'` and no
/// `"`. Non-ASCII text is rejected, because Python's `isprintable` rules differ
/// from Rust's and a silent mismatch would split one query into two fingerprints.
fn py_str_repr(t: &str) -> Result<String> {
    if !t.is_ascii() {
        return Err(Error::parse(
            "non-ASCII text literal cannot be fingerprinted",
        ));
    }
    let quote = if t.contains('\'') && !t.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(t.len() + 2);
    out.push(quote);
    for c in t.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    Ok(out)
}

/// One `WHERE` conjunct. `op` is the canonical spelling (`exact`, `ne`, `gt`,
/// `gte`, `lt`, `lte`), not the SQL symbol.
#[derive(Debug, Clone, PartialEq)]
pub struct Predicate {
    pub column: String,
    pub op: String,
    pub value: Literal,
}

/// The parsed shape of a query, reduced to what the fingerprint covers.
#[derive(Debug, Clone, PartialEq)]
pub struct CanonicalQuery {
    /// `COUNT`, `SUM` or `AVG`.
    pub agg: String,
    /// `None` for `COUNT(*)`.
    pub column: Option<String>,
    pub table: String,
    pub predicates: Vec<Predicate>,
    pub group_by: Option<String>,
}

impl CanonicalQuery {
    /// The canonical string. Predicates are sorted by `(column, op, str(value))`
    /// so clause order does not change it.
    pub fn canonical(&self) -> Result<String> {
        let mut keyed: Vec<(&Predicate, String)> = self
            .predicates
            .iter()
            .map(|p| p.value.py_str().map(|s| (p, s)))
            .collect::<Result<_>>()?;
        keyed.sort_by(|(a, sa), (b, sb)| {
            (a.column.as_str(), a.op.as_str(), sa.as_str()).cmp(&(
                b.column.as_str(),
                b.op.as_str(),
                sb.as_str(),
            ))
        });

        let mut parts = vec![
            self.agg.clone(),
            self.column.clone().unwrap_or_else(|| "*".to_string()),
            self.table.clone(),
        ];
        for (p, _) in keyed {
            parts.push(format!("{}{}{}", p.column, p.op, p.value.py_repr()?));
        }
        parts.push(format!("GROUP={}", self.group_by.as_deref().unwrap_or("")));
        Ok(parts.join("|"))
    }

    /// SHA-256 fingerprint of the canonical query string (SPEC.md §4.6).
    pub fn fingerprint(&self) -> Result<QueryFingerprint> {
        let canon = self.canonical()?;
        Ok(QueryFingerprint::from_digest(crate::mechanisms::sha256(
            canon.as_bytes(),
        )))
    }
}

/// A 32-byte SHA-256 query fingerprint.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct QueryFingerprint([u8; 32]);

impl QueryFingerprint {
    pub const fn from_digest(digest: [u8; 32]) -> Self {
        QueryFingerprint(digest)
    }

    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    pub fn to_hex(&self) -> String {
        use std::fmt::Write as _;
        let mut out = String::with_capacity(64);
        for b in &self.0 {
            let _ = write!(out, "{b:02x}");
        }
        out
    }

    pub fn from_hex(s: &str) -> Result<Self> {
        if s.len() != 64 || !s.is_ascii() {
            return Err(Error::engine("fingerprint must be 64 hex characters"));
        }
        let mut out = [0u8; 32];
        for (i, byte) in out.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&s[2 * i..2 * i + 2], 16)
                .map_err(|_| Error::engine("fingerprint is not valid hex"))?;
        }
        Ok(QueryFingerprint(out))
    }
}

impl fmt::Display for QueryFingerprint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_hex())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pred(c: &str, op: &str, v: Literal) -> Predicate {
        Predicate {
            column: c.into(),
            op: op.into(),
            value: v,
        }
    }

    fn q(
        agg: &str,
        col: Option<&str>,
        preds: Vec<Predicate>,
        group: Option<&str>,
    ) -> CanonicalQuery {
        CanonicalQuery {
            agg: agg.into(),
            column: col.map(Into::into),
            table: "visits".into(),
            predicates: preds,
            group_by: group.map(Into::into),
        }
    }

    // The expected strings below were produced by the Python oracle
    // (`QuerySpec.canonical()`), not written by hand.
    #[test]
    fn matches_the_oracle_for_a_bare_count() {
        assert_eq!(
            q("COUNT", None, vec![], None).canonical().unwrap(),
            "COUNT|*|visits|GROUP="
        );
    }

    #[test]
    fn matches_the_oracle_and_ignores_predicate_order() {
        let a = vec![
            pred("dept_id", "exact", Literal::Int(2)),
            pred("total_charge", "gt", Literal::Float(500.0)),
        ];
        let mut b = a.clone();
        b.reverse();
        let want = "COUNT|*|visits|dept_idexact2|total_chargegt500.0|GROUP=";
        assert_eq!(q("COUNT", None, a, None).canonical().unwrap(), want);
        assert_eq!(q("COUNT", None, b, None).canonical().unwrap(), want);
    }

    #[test]
    fn matches_the_oracle_for_a_grouped_sum() {
        let p = vec![pred("total_charge", "gte", Literal::Float(0.0))];
        assert_eq!(
            q("SUM", Some("total_charge"), p, Some("dept_id"))
                .canonical()
                .unwrap(),
            "SUM|total_charge|visits|total_chargegte0.0|GROUP=dept_id"
        );
    }

    #[test]
    fn text_literals_use_python_repr_quoting() {
        let p = vec![pred(
            "admission_type",
            "exact",
            Literal::Text("EMERGENCY".into()),
        )];
        assert_eq!(
            q("AVG", Some("total_charge"), p, None).canonical().unwrap(),
            "AVG|total_charge|visits|admission_typeexact'EMERGENCY'|GROUP="
        );
        // Python switches to double quotes when the text holds a single quote.
        let p = vec![pred(
            "admission_type",
            "exact",
            Literal::Text("O'Brien".into()),
        )];
        assert_eq!(
            q("COUNT", None, p, None).canonical().unwrap(),
            "COUNT|*|visits|admission_typeexact\"O'Brien\"|GROUP="
        );
    }

    #[test]
    fn int_and_text_one_are_different_fingerprints() {
        let a = q(
            "COUNT",
            None,
            vec![pred("dept_id", "exact", Literal::Int(1))],
            None,
        );
        let b = q(
            "COUNT",
            None,
            vec![pred("dept_id", "exact", Literal::Text("1".into()))],
            None,
        );
        assert_ne!(a.canonical().unwrap(), b.canonical().unwrap());
    }

    #[test]
    fn unrepresentable_literals_are_refused_not_mis_fingerprinted() {
        for lit in [
            Literal::Float(f64::NAN),
            Literal::Float(f64::INFINITY),
            Literal::Float(1e300),
            Literal::Text("café".into()),
        ] {
            let query = q("COUNT", None, vec![pred("total_charge", "gt", lit)], None);
            assert!(query.canonical().is_err());
        }
    }

    #[test]
    fn hex_round_trips_and_rejects_garbage() {
        let hex = "edc6f9946aed8757f617dac4a6ea3c1120f72cbaac6267577007b2db661238a5";
        let fp = QueryFingerprint::from_hex(hex).unwrap();
        assert_eq!(fp.to_hex(), hex);
        assert_eq!(fp.to_string(), hex);
        assert_eq!(QueryFingerprint::from_digest(*fp.as_bytes()), fp);
        assert!(QueryFingerprint::from_hex("abc").is_err());
        assert!(QueryFingerprint::from_hex(&"zz".repeat(32)).is_err());
    }
}
