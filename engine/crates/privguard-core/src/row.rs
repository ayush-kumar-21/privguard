//! The executor <-> operator seam. Phase 0 freeze.
//!
//! The executor (heap scan, filter, group-by) produces rows; the DP operators
//! (`Clamp -> ContributionBound -> DPAggregate -> PartitionSelect`) consume them.
//! Both sides depend only on [`RowSource`], so the operators can be tested over
//! in-memory rows before the real storage layer exists.

use crate::{Result, Value};

/// One tuple: values in column order of the producing operator's schema.
pub type Row = Vec<Value>;

/// A pull-based stream of rows (Volcano style).
///
/// `next_row` returns `Ok(None)` once, at end of stream; implementations must keep
/// returning `Ok(None)` afterwards. Row order is deterministic for a given source,
/// because contribution bounding depends on it (`SPEC.md` A4.2).
pub trait RowSource {
    fn next_row(&mut self) -> Result<Option<Row>>;

    /// Adapter for tests and simple consumers.
    fn collect_rows(&mut self) -> Result<Vec<Row>> {
        let mut out = Vec::new();
        while let Some(row) = self.next_row()? {
            out.push(row);
        }
        Ok(out)
    }
}

/// Boxed sources are sources, so operators can be stacked at run time (`Box<dyn RowSource>`).
impl<T: RowSource + ?Sized> RowSource for Box<T> {
    fn next_row(&mut self) -> Result<Option<Row>> {
        (**self).next_row()
    }
}

/// In-memory source over a vector of rows.
#[derive(Debug, Clone)]
pub struct VecRowSource {
    rows: std::vec::IntoIter<Row>,
}

impl VecRowSource {
    pub fn new(rows: Vec<Row>) -> Self {
        VecRowSource {
            rows: rows.into_iter(),
        }
    }
}

impl RowSource for VecRowSource {
    fn next_row(&mut self) -> Result<Option<Row>> {
        Ok(self.rows.next())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Error;

    #[test]
    fn vec_source_yields_rows_in_order_then_none() {
        let mut src = VecRowSource::new(vec![
            vec![Value::I32(1), Value::Text("a".into())],
            vec![Value::I32(2), Value::Null],
        ]);
        assert_eq!(
            src.next_row().unwrap(),
            Some(vec![Value::I32(1), Value::Text("a".into())])
        );
        assert_eq!(
            src.next_row().unwrap(),
            Some(vec![Value::I32(2), Value::Null])
        );
        assert_eq!(src.next_row().unwrap(), None);
        // Fused: stays exhausted.
        assert_eq!(src.next_row().unwrap(), None);
    }

    #[test]
    fn collect_rows_drains_the_source() {
        let mut src = VecRowSource::new(vec![vec![Value::Bool(true)], vec![Value::Bool(false)]]);
        assert_eq!(src.collect_rows().unwrap().len(), 2);
        assert!(src.collect_rows().unwrap().is_empty());
    }

    #[test]
    fn errors_propagate_through_collect() {
        struct Failing;
        impl RowSource for Failing {
            fn next_row(&mut self) -> Result<Option<Row>> {
                Err(Error::engine("boom"))
            }
        }
        assert_eq!(Failing.collect_rows(), Err(Error::engine("boom")));
    }

    #[test]
    fn trait_is_object_safe() {
        let mut boxed: Box<dyn RowSource> = Box::new(VecRowSource::new(vec![vec![Value::I64(7)]]));
        assert_eq!(boxed.next_row().unwrap(), Some(vec![Value::I64(7)]));
    }
}
