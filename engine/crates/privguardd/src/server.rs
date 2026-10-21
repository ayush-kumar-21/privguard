//! The pgwire front end: a tokio TCP server speaking the `SPEC.md` §8 subset of protocol v3.
//!
//! Authentication model (`SPEC.md` §10 A2): the FastAPI gateway authenticates humans with
//! argon2 and JWTs, then connects here as the *service*. The startup `user` names the principal
//! on whose behalf it acts; the cleartext password is the engine's service secret. So only a
//! holder of the secret can speak as any principal, which is why the listener binds to
//! localhost by default.
//!
//! Statements (simple query protocol only):
//! `SELECT ...` (a private aggregate), `EXPLAIN SELECT ...`, `SET privguard.rho = <x>`,
//! `SHOW budget | budgets | ledger | queries`, and the transaction no-ops `BEGIN`/`COMMIT`/
//! `ROLLBACK` that client libraries send on their own.

use crate::engine::{Engine, QueryOutput};
use privguard_core::{Error, Result};
use privguard_privacy::defaults;
use privguard_wire::codec::{
    self, format_value, parse_frontend, parse_startup, type_oid, Frontend, Startup,
    MAX_MESSAGE_LEN, OID_BOOL, OID_FLOAT8, OID_INT8, OID_TEXT,
};
use std::io;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

/// How long a client may take to finish the startup handshake.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(10);
/// Startup packets are tiny; refuse anything bigger rather than buffer it.
const MAX_STARTUP_LEN: usize = 10_000;

/// The result of one statement, ready to be framed.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Reply {
    pub columns: Vec<(String, i32)>,
    pub rows: Vec<Vec<Option<String>>>,
    pub tag: String,
    /// DP metadata, sent as a `NoticeResponse` (JSON).
    pub notice: Option<String>,
    pub empty: bool,
}

impl Reply {
    fn command(tag: &str) -> Reply {
        Reply {
            tag: tag.to_string(),
            ..Reply::default()
        }
    }

    fn table(columns: &[(&str, i32)], rows: Vec<Vec<Option<String>>>) -> Reply {
        Reply {
            columns: columns
                .iter()
                .map(|(n, o)| ((*n).to_string(), *o))
                .collect(),
            tag: format!("SELECT {}", rows.len()),
            rows,
            ..Reply::default()
        }
    }
}

fn some(s: impl ToString) -> Option<String> {
    Some(s.to_string())
}

fn balance_status(remaining: f64) -> &'static str {
    if remaining > 0.0 {
        "active"
    } else {
        "exhausted"
    }
}

fn notice_json(out: &QueryOutput) -> String {
    use privguard_core::json::Json;
    let b = &out.budget;
    Json::Obj(vec![
        ("status".into(), Json::str(out.status)),
        ("rho_charged".into(), Json::float(out.rho_charged)),
        ("epsilon_charged".into(), Json::float(out.epsilon_charged)),
        ("mechanism".into(), Json::str(&out.mechanism)),
        ("query_id".into(), Json::int(out.query_id as i64)),
        (
            "suppressed_groups".into(),
            Json::int(out.suppressed_groups as i64),
        ),
        (
            "budget".into(),
            Json::Obj(vec![
                ("rho_total".into(), Json::float(b.total)),
                ("rho_spent".into(), Json::float(b.spent)),
                ("rho_reserved".into(), Json::float(b.reserved)),
                ("rho_remaining".into(), Json::float(b.remaining())),
                ("status".into(), Json::str(balance_status(b.remaining()))),
            ]),
        ),
    ])
    .to_json_string()
}

fn parse_rho_setting(rest: &str) -> Result<f64> {
    let value = rest
        .trim_start_matches(|c: char| c == '=' || c.is_whitespace())
        .trim_start_matches("to")
        .trim()
        .trim_matches('\'');
    value
        .parse::<f64>()
        .map_err(|_| Error::policy(format!("invalid value for privguard.rho: {value}")))
}

/// Run one statement for `user`. `rho` is the session's `privguard.rho` setting.
pub fn execute_statement(
    engine: &mut Engine,
    user: &str,
    rho: &mut f64,
    sql: &str,
) -> Result<Reply> {
    let stmt = sql.trim().trim_end_matches(';').trim();
    if stmt.is_empty() {
        return Ok(Reply {
            empty: true,
            ..Reply::default()
        });
    }
    let lower = stmt.to_ascii_lowercase();
    let word = lower.split_whitespace().next().unwrap_or("");

    match word {
        "select" => {
            let out = engine.query(user, stmt, *rho)?;
            let columns = out
                .columns
                .iter()
                .map(|(n, t)| (n.clone(), type_oid(*t)))
                .collect();
            let rows: Vec<_> = out
                .rows
                .iter()
                .map(|r| r.iter().map(format_value).collect())
                .collect();
            Ok(Reply {
                columns,
                tag: format!("SELECT {}", rows.len()),
                rows,
                notice: Some(notice_json(&out)),
                empty: false,
            })
        }
        "explain" => {
            let plan = engine.explain(user, stmt["explain".len()..].trim())?;
            Ok(Reply::table(&[("plan", OID_TEXT)], vec![vec![Some(plan)]]))
        }
        "set" => {
            // `SET privguard.rho = 0.1` / `SET SESSION privguard.rho TO '0.1'`
            if let Some(at) = lower.find("privguard.rho") {
                *rho = parse_rho_setting(&stmt[at + "privguard.rho".len()..])?;
            }
            Ok(Reply::command("SET"))
        }
        "reset" | "discard" => Ok(Reply::command(&word.to_ascii_uppercase())),
        "begin" | "start" => Ok(Reply::command("BEGIN")),
        "commit" | "end" => Ok(Reply::command("COMMIT")),
        "rollback" | "abort" => Ok(Reply::command("ROLLBACK")),
        "show" => show(engine, user, lower["show".len()..].trim()),
        _ => Err(Error::parse(format!(
            "unsupported statement: {}",
            word.to_ascii_uppercase()
        ))),
    }
}

fn show(engine: &mut Engine, user: &str, what: &str) -> Result<Reply> {
    let budget_cols = [
        ("account_id", OID_INT8),
        ("principal_name", OID_TEXT),
        ("rho_total", OID_FLOAT8),
        ("rho_spent", OID_FLOAT8),
        ("rho_reserved", OID_FLOAT8),
        ("rho_remaining", OID_FLOAT8),
        ("status", OID_TEXT),
    ];
    let budget_row = |name: &str, account: u64, b: &privguard_privacy::ledger::Balance| {
        vec![
            some(account),
            some(name),
            some(b.total),
            some(b.spent),
            some(b.reserved),
            some(b.remaining()),
            some(balance_status(b.remaining())),
        ]
    };
    match what {
        "budget" => {
            let rows = match engine.balance(user)? {
                Some((account, b)) => vec![budget_row(user, account, &b)],
                None => vec![],
            };
            Ok(Reply::table(&budget_cols, rows))
        }
        "budgets" => {
            let rows = engine
                .balances(user)?
                .iter()
                .map(|(name, account, b)| budget_row(name, *account, b))
                .collect();
            Ok(Reply::table(&budget_cols, rows))
        }
        "ledger" => {
            let rows = match engine.verify_ledger(user)? {
                Some(v) => vec![vec![
                    some(if v.ok { "t" } else { "f" }),
                    some(v.entries),
                    v.first_bad_entry.map(|n| n.to_string()),
                ]],
                None => vec![],
            };
            Ok(Reply::table(
                &[
                    ("ok", OID_BOOL),
                    ("entries", OID_INT8),
                    ("first_bad_entry", OID_INT8),
                ],
                rows,
            ))
        }
        "queries" => {
            let rows = engine
                .audit_rows(user)?
                .into_iter()
                .map(|(name, a)| {
                    vec![
                        some(a.query_id),
                        some(name),
                        some(a.status),
                        some(a.rho),
                        some(0.0),
                        some(codec::format_timestamp(a.ts_micros)),
                        some(a.sql),
                    ]
                })
                .collect();
            Ok(Reply::table(
                &[
                    ("query_id", OID_INT8),
                    ("principal_name", OID_TEXT),
                    ("status", OID_TEXT),
                    ("rho_charged", OID_FLOAT8),
                    ("delta_charged", OID_FLOAT8),
                    ("submitted_at", OID_TEXT),
                    ("sql_text", OID_TEXT),
                ],
                rows,
            ))
        }
        other => Err(Error::parse(format!(
            "unsupported SHOW {other}: expected budget, budgets, ledger or queries"
        ))),
    }
}

// ------------------------------------------------------------------- framing

fn encode_reply(reply: &Reply) -> Vec<u8> {
    let mut out = Vec::new();
    if reply.empty {
        out.extend(codec::empty_query_response());
        return out;
    }
    if !reply.columns.is_empty() {
        out.extend(codec::row_description(&reply.columns));
        for row in &reply.rows {
            out.extend(codec::data_row(row));
        }
    }
    if let Some(n) = &reply.notice {
        out.extend(codec::notice_response(n));
    }
    out.extend(codec::command_complete(&reply.tag));
    out
}

fn encode_error(e: &Error) -> Vec<u8> {
    codec::error_response(e.sqlstate().code(), &e.to_string())
}

fn bad_data(msg: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, msg.to_string())
}

/// Constant-time comparison, so response timing does not reveal how much of a guess matched.
fn secrets_equal(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    let mut diff = a.len() ^ b.len();
    for i in 0..a.len().max(b.len()) {
        diff |= usize::from(a.get(i).copied().unwrap_or(0) ^ b.get(i).copied().unwrap_or(0));
    }
    diff == 0
}

async fn read_startup_packet(s: &mut TcpStream) -> io::Result<Vec<u8>> {
    let len = s.read_u32().await? as usize;
    if !(8..=MAX_STARTUP_LEN).contains(&len) {
        return Err(bad_data("startup packet length out of range"));
    }
    let mut body = vec![0u8; len - 4];
    s.read_exact(&mut body).await?;
    Ok(body)
}

async fn read_frontend(s: &mut TcpStream) -> io::Result<Option<(u8, Vec<u8>)>> {
    let tag = match s.read_u8().await {
        Ok(t) => t,
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e),
    };
    let len = s.read_i32().await?;
    if len < 4 || len as usize - 4 > MAX_MESSAGE_LEN {
        return Err(bad_data("message length out of range"));
    }
    let mut body = vec![0u8; len as usize - 4];
    s.read_exact(&mut body).await?;
    Ok(Some((tag, body)))
}

/// Accept connections until the listener fails. One task per connection; the engine is a single
/// writer behind a mutex (`SPEC.md` §1).
pub async fn serve(
    listener: TcpListener,
    engine: Arc<Mutex<Engine>>,
    service_secret: Arc<String>,
) -> io::Result<()> {
    loop {
        let (stream, _) = listener.accept().await?;
        let (engine, secret) = (Arc::clone(&engine), Arc::clone(&service_secret));
        tokio::spawn(async move {
            // A broken connection is the client's problem, not the server's.
            let _ = handle(stream, engine, secret).await;
        });
    }
}

async fn handle(
    mut s: TcpStream,
    engine: Arc<Mutex<Engine>>,
    secret: Arc<String>,
) -> io::Result<()> {
    s.set_nodelay(true)?;

    let user = match tokio::time::timeout(STARTUP_TIMEOUT, startup(&mut s, &engine, &secret)).await
    {
        Ok(Ok(Some(user))) => user,
        Ok(Ok(None)) => return Ok(()),
        Ok(Err(e)) => return Err(e),
        Err(_) => return Err(bad_data("startup timed out")),
    };

    let mut rho = defaults::RHO;
    while let Some((tag, body)) = read_frontend(&mut s).await? {
        let reply = match parse_frontend(tag, &body) {
            Ok(Frontend::Query(sql)) => {
                let engine = Arc::clone(&engine);
                let user = user.clone();
                let result = tokio::task::spawn_blocking(move || {
                    let mut e = engine
                        .lock()
                        .map_err(|_| Error::engine("engine lock poisoned"))?;
                    let reply = execute_statement(&mut e, &user, &mut rho, &sql);
                    reply.map(|r| (r, rho))
                })
                .await
                .map_err(|_| io::Error::other("query task failed"))?;
                match result {
                    Ok((reply, new_rho)) => {
                        rho = new_rho;
                        encode_reply(&reply)
                    }
                    Err(e) => encode_error(&e),
                }
            }
            Ok(Frontend::Terminate) => return Ok(()),
            Ok(Frontend::Unsupported(t)) => codec::error_response(
                "0A000",
                &format!(
                    "message type '{}' is not supported: only the simple query protocol is",
                    t as char
                ),
            ),
            Ok(Frontend::Password(_)) => {
                codec::error_response("08P01", "unexpected password message")
            }
            Err(e) => encode_error(&e),
        };
        s.write_all(&reply).await?;
        s.write_all(&codec::ready_for_query()).await?;
    }
    Ok(())
}

/// The handshake. `Ok(Some(user))` once authenticated; `Ok(None)` if the client left politely.
async fn startup(
    s: &mut TcpStream,
    engine: &Arc<Mutex<Engine>>,
    secret: &str,
) -> io::Result<Option<String>> {
    let params = loop {
        let body = read_startup_packet(s).await?;
        match parse_startup(&body) {
            // No TLS here: say so and let the client retry in the clear.
            Ok(Startup::SslRequest | Startup::GssEncRequest) => s.write_all(b"N").await?,
            Ok(Startup::Cancel) => return Ok(None),
            Ok(Startup::Params(p)) => break p,
            Err(e) => {
                s.write_all(&codec::fatal_response(e.sqlstate().code(), &e.to_string()))
                    .await?;
                return Ok(None);
            }
        }
    };

    let Some(user) = params.get("user").cloned() else {
        s.write_all(&codec::fatal_response("28000", "no user given"))
            .await?;
        return Ok(None);
    };

    s.write_all(&codec::auth_cleartext_request()).await?;
    let password = match read_frontend(s).await? {
        Some((tag, body)) => match parse_frontend(tag, &body) {
            Ok(Frontend::Password(p)) => p,
            _ => {
                s.write_all(&codec::fatal_response(
                    "08P01",
                    "expected a password message",
                ))
                .await?;
                return Ok(None);
            }
        },
        None => return Ok(None),
    };
    if !secrets_equal(&password, secret) {
        s.write_all(&codec::fatal_response(
            "28P01",
            "password authentication failed",
        ))
        .await?;
        return Ok(None);
    }
    let known = engine
        .lock()
        .map(|e| e.principal(&user).is_some())
        .unwrap_or(false);
    if !known {
        s.write_all(&codec::fatal_response(
            "28000",
            &format!("role \"{user}\" does not exist"),
        ))
        .await?;
        return Ok(None);
    }

    let mut hello = codec::auth_ok();
    for (k, v) in [
        ("server_version", "14.0 (PrivGuard)"),
        ("server_encoding", "UTF8"),
        ("client_encoding", "UTF8"),
        ("DateStyle", "ISO, MDY"),
        ("integer_datetimes", "on"),
        ("standard_conforming_strings", "on"),
    ] {
        hello.extend(codec::parameter_status(k, v));
    }
    hello.extend(codec::backend_key_data(std::process::id() as i32, 0));
    hello.extend(codec::ready_for_query());
    s.write_all(&hello).await?;
    Ok(Some(user))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secrets_compare_by_content_and_length() {
        assert!(secrets_equal("abc", "abc"));
        assert!(!secrets_equal("abc", "abd"));
        assert!(!secrets_equal("abc", "abcd"));
        assert!(!secrets_equal("", "a"));
        assert!(secrets_equal("", ""));
    }

    #[test]
    fn rho_settings_accept_the_common_spellings() {
        for s in [" = 0.25", " TO 0.25", " = '0.25'", "=0.25 "] {
            assert_eq!(
                parse_rho_setting(&s.to_ascii_lowercase()).unwrap(),
                0.25,
                "{s:?}"
            );
        }
        assert!(parse_rho_setting(" = abc").is_err());
    }

    #[test]
    fn replies_frame_rows_notice_and_completion_in_order() {
        let reply = Reply {
            columns: vec![("value".into(), OID_INT8)],
            rows: vec![vec![some(196)]],
            tag: "SELECT 1".into(),
            notice: Some("{}".into()),
            empty: false,
        };
        let bytes = encode_reply(&reply);
        let mut tags = Vec::new();
        let mut rest = &bytes[..];
        while let Some((tag, _, next)) = codec::split_backend(rest) {
            tags.push(tag);
            rest = next;
        }
        assert_eq!(tags, [b'T', b'D', b'N', b'C']);
        assert_eq!(
            codec::split_backend(&encode_reply(&Reply {
                empty: true,
                ..Reply::default()
            }))
            .unwrap()
            .0,
            b'I'
        );
    }
}
