//! The engine error taxonomy, frozen in `SPEC.md` §9.
//!
//! Each variant carries the PostgreSQL SQLSTATE it is reported as on the wire,
//! so the mapping lives with the error rather than in the protocol layer where
//! it would drift.
//!
//! Implemented by hand rather than with `thiserror`: the foundation phase
//! carries no external dependencies, so the whole workspace builds with no
//! network access.

use std::fmt;

/// SQLSTATE codes the engine reports. See `SPEC.md` §9.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SqlState {
    /// `42601` — syntax error, i.e. outside the supported grammar.
    SyntaxError,
    /// `42501` — insufficient privilege, used for all policy refusals.
    InsufficientPrivilege,
    /// `53400` — configuration limit exceeded, used for budget exhaustion.
    ConfigurationLimitExceeded,
    /// `42P01` — undefined table, used for all catalog misses.
    UndefinedTable,
    /// `XX000` — internal error.
    InternalError,
}

impl SqlState {
    pub const fn code(self) -> &'static str {
        match self {
            SqlState::SyntaxError => "42601",
            SqlState::InsufficientPrivilege => "42501",
            SqlState::ConfigurationLimitExceeded => "53400",
            SqlState::UndefinedTable => "42P01",
            SqlState::InternalError => "XX000",
        }
    }
}

impl fmt::Display for SqlState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}

/// Anything the engine can refuse to do.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// The statement is outside the supported grammar (`SPEC.md` §3).
    Parse(String),
    /// The statement parses but policy forbids it — missing clamp bounds, an
    /// ungroupable column, a denied column.
    Policy(String),
    /// The budget reservation was refused. Not a failure: the expected answer
    /// when an analyst has spent their allowance.
    BudgetExhausted(String),
    /// Unknown database, table or column.
    Catalog(String),
    /// An engine invariant was violated. Always a defect.
    Engine(String),
}

impl Error {
    pub fn sqlstate(&self) -> SqlState {
        match self {
            Error::Parse(_) => SqlState::SyntaxError,
            Error::Policy(_) => SqlState::InsufficientPrivilege,
            Error::BudgetExhausted(_) => SqlState::ConfigurationLimitExceeded,
            Error::Catalog(_) => SqlState::UndefinedTable,
            Error::Engine(_) => SqlState::InternalError,
        }
    }

    pub fn parse(msg: impl Into<String>) -> Self {
        Error::Parse(msg.into())
    }
    pub fn policy(msg: impl Into<String>) -> Self {
        Error::Policy(msg.into())
    }
    pub fn budget(msg: impl Into<String>) -> Self {
        Error::BudgetExhausted(msg.into())
    }
    pub fn catalog(msg: impl Into<String>) -> Self {
        Error::Catalog(msg.into())
    }
    pub fn engine(msg: impl Into<String>) -> Self {
        Error::Engine(msg.into())
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let (label, msg) = match self {
            Error::Parse(m) => ("parse error", m),
            Error::Policy(m) => ("policy error", m),
            Error::BudgetExhausted(m) => ("budget exhausted", m),
            Error::Catalog(m) => ("catalog error", m),
            Error::Engine(m) => ("engine error", m),
        };
        write!(f, "{label}: {msg}")
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sqlstate_mapping_matches_spec() {
        // SPEC.md §9. These codes are part of the wire contract; a client can
        // branch on them, so changing one is a breaking change.
        assert_eq!(Error::parse("x").sqlstate().code(), "42601");
        assert_eq!(Error::policy("x").sqlstate().code(), "42501");
        assert_eq!(Error::budget("x").sqlstate().code(), "53400");
        assert_eq!(Error::catalog("x").sqlstate().code(), "42P01");
        assert_eq!(Error::engine("x").sqlstate().code(), "XX000");
    }

    #[test]
    fn display_includes_class_and_message() {
        assert_eq!(
            Error::policy("SUM(total_charge) needs clamp bounds").to_string(),
            "policy error: SUM(total_charge) needs clamp bounds"
        );
    }
}
