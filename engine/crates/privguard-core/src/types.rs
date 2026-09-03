//! Physical types and the record identifier. `SPEC.md` §2.3 and §5.1.

use std::fmt;

/// Page size, frozen at 8 KiB (`SPEC.md` §5.1). Everything in the storage layer
/// — slot offsets, the free-space map, B+ tree fanout — is sized off this.
pub const PAGE_SIZE: usize = 8192;

/// The physical types the engine stores. `SPEC.md` §2.3.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnType {
    I32,
    I64,
    F64,
    Text,
    /// Microseconds since the Unix epoch, stored as an `I64`.
    Timestamp,
    Bool,
}

impl ColumnType {
    /// Serialized width in bytes, or `None` for variable-width types.
    pub const fn fixed_width(self) -> Option<usize> {
        match self {
            ColumnType::I32 => Some(4),
            ColumnType::I64 | ColumnType::F64 | ColumnType::Timestamp => Some(8),
            ColumnType::Bool => Some(1),
            ColumnType::Text => None,
        }
    }

    /// Whether `SUM`/`AVG` may target this type. Aggregating a timestamp or a
    /// bool is almost always a mistake, and neither has a meaningful clamp
    /// range, so the binder rejects it (`SPEC.md` §3).
    pub const fn is_numeric(self) -> bool {
        matches!(self, ColumnType::I32 | ColumnType::I64 | ColumnType::F64)
    }
}

impl fmt::Display for ColumnType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            ColumnType::I32 => "i32",
            ColumnType::I64 => "i64",
            ColumnType::F64 => "f64",
            ColumnType::Text => "text",
            ColumnType::Timestamp => "timestamp",
            ColumnType::Bool => "bool",
        };
        f.write_str(s)
    }
}

/// A single stored value. `Null` is represented here for convenience, but on a
/// page nullness lives in the tuple's null bitmap, not as a tag byte.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    I32(i32),
    I64(i64),
    F64(f64),
    Text(String),
    /// Microseconds since the Unix epoch.
    Timestamp(i64),
    Bool(bool),
}

impl Value {
    pub fn type_of(&self) -> Option<ColumnType> {
        Some(match self {
            Value::Null => return None,
            Value::I32(_) => ColumnType::I32,
            Value::I64(_) => ColumnType::I64,
            Value::F64(_) => ColumnType::F64,
            Value::Text(_) => ColumnType::Text,
            Value::Timestamp(_) => ColumnType::Timestamp,
            Value::Bool(_) => ColumnType::Bool,
        })
    }

    pub const fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }

    /// Numeric widening for aggregation and clamping. Returns `None` for
    /// non-numeric values *and* for `Null` — callers decide whether a null
    /// contributes zero or is skipped, since the two differ between `SUM` and
    /// `COUNT`.
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::I32(v) => Some(*v as f64),
            Value::I64(v) => Some(*v as f64),
            Value::F64(v) => Some(*v),
            _ => None,
        }
    }
}

/// Record identifier: which page, which slot. `SPEC.md` §5.1.
///
/// A RID is stable for the life of a tuple — the slot directory is never
/// compacted in a way that moves a live slot's index, only its offset — which
/// is what lets a B+ tree leaf store RIDs rather than tuples.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Rid {
    pub page_id: u32,
    pub slot: u16,
}

impl Rid {
    pub const fn new(page_id: u32, slot: u16) -> Self {
        Rid { page_id, slot }
    }

    /// Packed form used as a B+ tree payload and in the free-space map.
    pub const fn to_u64(self) -> u64 {
        ((self.page_id as u64) << 16) | self.slot as u64
    }

    pub const fn from_u64(v: u64) -> Self {
        Rid {
            page_id: (v >> 16) as u32,
            slot: (v & 0xFFFF) as u16,
        }
    }
}

impl fmt::Display for Rid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({}, {})", self.page_id, self.slot)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_size_is_frozen_at_8_kib() {
        assert_eq!(PAGE_SIZE, 8192);
    }

    #[test]
    fn rid_packing_round_trips() {
        for rid in [
            Rid::new(0, 0),
            Rid::new(1, 7),
            Rid::new(u32::MAX, u16::MAX),
            Rid::new(123_456, 42),
        ] {
            assert_eq!(Rid::from_u64(rid.to_u64()), rid);
        }
    }

    #[test]
    fn rid_orders_by_page_then_slot() {
        // Sequential scan relies on this ordering to yield tuples in physical
        // order, which is in turn what makes contribution bounding
        // deterministic (SPEC.md §4.2).
        let mut rids = [Rid::new(2, 0), Rid::new(1, 5), Rid::new(1, 2)];
        rids.sort();
        assert_eq!(rids, [Rid::new(1, 2), Rid::new(1, 5), Rid::new(2, 0)]);
    }

    #[test]
    fn only_true_numerics_are_aggregatable() {
        assert!(ColumnType::F64.is_numeric());
        assert!(ColumnType::I64.is_numeric());
        // Rejected by the binder for SUM/AVG — no meaningful clamp range.
        assert!(!ColumnType::Timestamp.is_numeric());
        assert!(!ColumnType::Bool.is_numeric());
        assert!(!ColumnType::Text.is_numeric());
    }

    #[test]
    fn null_has_no_type_and_no_numeric_value() {
        assert_eq!(Value::Null.type_of(), None);
        assert_eq!(Value::Null.as_f64(), None);
        assert!(Value::Null.is_null());
    }

    #[test]
    fn fixed_widths_match_spec() {
        assert_eq!(ColumnType::I32.fixed_width(), Some(4));
        assert_eq!(ColumnType::Timestamp.fixed_width(), Some(8));
        assert_eq!(ColumnType::Bool.fixed_width(), Some(1));
        assert_eq!(ColumnType::Text.fixed_width(), None);
    }
}
