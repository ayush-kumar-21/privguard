//! PostgreSQL wire protocol v3, server side. `SPEC.md` §8.
//!
//! Speaking the Postgres wire protocol rather than inventing one means the
//! FastAPI gateway talks to the engine with stock `psycopg3`, and a plain
//! `psql` can drive it during a demo.
//!
//! DP metadata — ρ charged, ε equivalent, mechanism, suppressed group count —
//! rides back as a `NoticeResponse` alongside the rows, so clients that know
//! nothing about privacy still work.
//!
//! Landed in Phase 4.3 (`IMPLEMENTATION_PLAN.md`).

/// Protocol version 3.0, as sent in the startup message.
pub const PROTOCOL_VERSION: i32 = 196_608; // (3 << 16) | 0

/// Message types the v1 server handles. `SPEC.md` §8.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Message {
    Startup,
    PasswordMessage,
    /// Simple query, `Q`. The only query path in v1 — no extended protocol.
    Query,
    RowDescription,
    DataRow,
    CommandComplete,
    /// Carries the DP metadata for a result.
    NoticeResponse,
    ErrorResponse,
    ReadyForQuery,
    Terminate,
}

impl Message {
    /// The protocol's single-byte tag, where it has one. The startup message
    /// is length-prefixed with no tag, hence the `Option`.
    pub const fn tag(self) -> Option<u8> {
        Some(match self {
            Message::Startup => return None,
            Message::PasswordMessage => b'p',
            Message::Query => b'Q',
            Message::RowDescription => b'T',
            Message::DataRow => b'D',
            Message::CommandComplete => b'C',
            Message::NoticeResponse => b'N',
            Message::ErrorResponse => b'E',
            Message::ReadyForQuery => b'Z',
            Message::Terminate => b'X',
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_version_decomposes_to_3_dot_0() {
        // The startup message packs the version as major<<16 | minor, so check
        // the halves rather than restating the literal.
        assert_eq!(PROTOCOL_VERSION >> 16, 3, "major version");
        assert_eq!(PROTOCOL_VERSION & 0xFFFF, 0, "minor version");
    }

    #[test]
    fn message_tags_match_the_postgres_protocol() {
        assert_eq!(Message::Query.tag(), Some(b'Q'));
        assert_eq!(Message::DataRow.tag(), Some(b'D'));
        assert_eq!(Message::ReadyForQuery.tag(), Some(b'Z'));
        assert_eq!(Message::ErrorResponse.tag(), Some(b'E'));
        // Startup is length-prefixed with no tag byte.
        assert_eq!(Message::Startup.tag(), None);
    }

    #[test]
    fn notice_and_error_are_distinct() {
        // DP metadata goes out as a Notice; confusing the two would turn every
        // successful private query into a client-side error.
        assert_ne!(Message::NoticeResponse.tag(), Message::ErrorResponse.tag());
    }
}
