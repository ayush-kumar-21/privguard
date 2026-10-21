//! Shared vocabulary for the PrivGuard engine.
//!
//! Every other crate depends on this one and nothing else depends on them, so
//! the types here are deliberately small and free of policy: an error taxonomy
//! (`SPEC.md` §9), the physical column types (§2.3), and the record identifier
//! that addresses a tuple inside a page (§5.1).

pub mod error;
pub mod row;
pub mod types;

pub use error::{Error, Result, SqlState};
pub use row::{Row, RowSource, VecRowSource};
pub use types::{ColumnType, Rid, Value, PAGE_SIZE};
