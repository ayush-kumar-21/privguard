//! PostgreSQL v3 message framing for the subset `SPEC.md` §8 lists: startup, cleartext
//! password, simple query, row results, notices and errors.
//!
//! Pure functions over byte slices, so the whole protocol surface is unit-testable without a
//! socket. Every backend message is `tag (1) | length (4, includes itself) | body`; the startup
//! message has no tag.

use privguard_core::{ColumnType, Error, Result, Value};
use std::collections::BTreeMap;

pub const SSL_REQUEST_CODE: i32 = 80_877_103;
pub const GSSENC_REQUEST_CODE: i32 = 80_877_104;
pub const CANCEL_REQUEST_CODE: i32 = 80_877_102;

/// Largest frontend message the server will buffer. A query is text; 1 MiB is generous.
pub const MAX_MESSAGE_LEN: usize = 1 << 20;

/// PostgreSQL type OIDs for the result columns the engine produces.
pub const OID_BOOL: i32 = 16;
pub const OID_INT8: i32 = 20;
pub const OID_TEXT: i32 = 25;
pub const OID_FLOAT8: i32 = 701;
pub const OID_TIMESTAMP: i32 = 1114;

pub fn type_oid(t: ColumnType) -> i32 {
    match t {
        ColumnType::Bool => OID_BOOL,
        ColumnType::I32 | ColumnType::I64 => OID_INT8,
        ColumnType::F64 => OID_FLOAT8,
        ColumnType::Text => OID_TEXT,
        ColumnType::Timestamp => OID_TIMESTAMP,
    }
}

/// Text-format rendering of a value; `None` is SQL `NULL`.
pub fn format_value(v: &Value) -> Option<String> {
    Some(match v {
        Value::Null => return None,
        Value::Bool(b) => (if *b { "t" } else { "f" }).to_string(),
        Value::I32(x) => x.to_string(),
        Value::I64(x) => x.to_string(),
        Value::F64(x) if x.is_nan() => "NaN".to_string(),
        Value::F64(x) if x.is_infinite() => {
            (if *x > 0.0 { "Infinity" } else { "-Infinity" }).to_string()
        }
        Value::F64(x) => x.to_string(),
        Value::Text(s) => s.clone(),
        Value::Timestamp(us) => format_timestamp(*us),
    })
}

/// `YYYY-MM-DD HH:MM:SS[.ffffff]` for microseconds since the Unix epoch (proleptic Gregorian).
pub fn format_timestamp(micros: i64) -> String {
    let secs = micros.div_euclid(1_000_000);
    let frac = micros.rem_euclid(1_000_000);
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400);
    // Howard Hinnant's civil-from-days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    let base = format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}",
        tod / 3600,
        tod % 3600 / 60,
        tod % 60
    );
    if frac == 0 {
        base
    } else {
        format!("{base}.{frac:06}")
    }
}

// ------------------------------------------------------------------ frontend

#[derive(Debug, Clone, PartialEq)]
pub enum Startup {
    /// Client asks to negotiate TLS; the server answers `N` and the client retries in the clear.
    SslRequest,
    GssEncRequest,
    Cancel,
    Params(BTreeMap<String, String>),
}

fn cstring(buf: &[u8], pos: &mut usize) -> Result<String> {
    let rest = buf
        .get(*pos..)
        .ok_or_else(|| Error::parse("truncated message"))?;
    let end = rest
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| Error::parse("string is not NUL-terminated"))?;
    let s = std::str::from_utf8(&rest[..end]).map_err(|_| Error::parse("string is not UTF-8"))?;
    *pos += end + 1;
    Ok(s.to_string())
}

/// Parse a startup packet body (the bytes after its 4-byte length).
pub fn parse_startup(body: &[u8]) -> Result<Startup> {
    let code = body
        .get(..4)
        .map(|b| i32::from_be_bytes(b.try_into().unwrap()))
        .ok_or_else(|| Error::parse("startup packet too short"))?;
    match code {
        SSL_REQUEST_CODE => Ok(Startup::SslRequest),
        GSSENC_REQUEST_CODE => Ok(Startup::GssEncRequest),
        CANCEL_REQUEST_CODE => Ok(Startup::Cancel),
        crate::PROTOCOL_VERSION => {
            let mut params = BTreeMap::new();
            let mut pos = 4;
            loop {
                let key = cstring(body, &mut pos)?;
                if key.is_empty() {
                    break;
                }
                params.insert(key, cstring(body, &mut pos)?);
            }
            Ok(Startup::Params(params))
        }
        other => Err(Error::parse(format!(
            "unsupported protocol version {}.{}",
            other >> 16,
            other & 0xFFFF
        ))),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Frontend {
    Password(String),
    Query(String),
    Terminate,
    /// Extended-query, COPY and every other message the server does not implement.
    Unsupported(u8),
}

/// Parse a tagged frontend message.
pub fn parse_frontend(tag: u8, body: &[u8]) -> Result<Frontend> {
    Ok(match tag {
        b'p' => Frontend::Password(cstring(body, &mut 0)?),
        b'Q' => Frontend::Query(cstring(body, &mut 0)?),
        b'X' => Frontend::Terminate,
        other => Frontend::Unsupported(other),
    })
}

// ------------------------------------------------------------------- backend

fn frame(tag: u8, body: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(5 + body.len());
    out.push(tag);
    out.extend_from_slice(&((body.len() + 4) as i32).to_be_bytes());
    out.extend_from_slice(body);
    out
}

fn push_cstr(out: &mut Vec<u8>, s: &str) {
    // A NUL inside would end the string early and shift every later field.
    out.extend(s.bytes().filter(|b| *b != 0));
    out.push(0);
}

pub fn auth_cleartext_request() -> Vec<u8> {
    frame(b'R', &3i32.to_be_bytes())
}

pub fn auth_ok() -> Vec<u8> {
    frame(b'R', &0i32.to_be_bytes())
}

pub fn parameter_status(name: &str, value: &str) -> Vec<u8> {
    let mut b = Vec::new();
    push_cstr(&mut b, name);
    push_cstr(&mut b, value);
    frame(b'S', &b)
}

pub fn backend_key_data(pid: i32, secret: i32) -> Vec<u8> {
    let mut b = pid.to_be_bytes().to_vec();
    b.extend_from_slice(&secret.to_be_bytes());
    frame(b'K', &b)
}

/// `I` = idle (no transaction; the engine has none).
pub fn ready_for_query() -> Vec<u8> {
    frame(b'Z', b"I")
}

pub fn row_description(columns: &[(String, i32)]) -> Vec<u8> {
    let mut b = (columns.len() as i16).to_be_bytes().to_vec();
    for (name, oid) in columns {
        push_cstr(&mut b, name);
        b.extend_from_slice(&0i32.to_be_bytes()); // table oid
        b.extend_from_slice(&0i16.to_be_bytes()); // column attribute number
        b.extend_from_slice(&oid.to_be_bytes());
        b.extend_from_slice(&(-1i16).to_be_bytes()); // type size: variable
        b.extend_from_slice(&(-1i32).to_be_bytes()); // type modifier
        b.extend_from_slice(&0i16.to_be_bytes()); // text format
    }
    frame(b'T', &b)
}

pub fn data_row(values: &[Option<String>]) -> Vec<u8> {
    let mut b = (values.len() as i16).to_be_bytes().to_vec();
    for v in values {
        match v {
            None => b.extend_from_slice(&(-1i32).to_be_bytes()),
            Some(s) => {
                b.extend_from_slice(&(s.len() as i32).to_be_bytes());
                b.extend_from_slice(s.as_bytes());
            }
        }
    }
    frame(b'D', &b)
}

pub fn command_complete(tag: &str) -> Vec<u8> {
    let mut b = Vec::new();
    push_cstr(&mut b, tag);
    frame(b'C', &b)
}

pub fn empty_query_response() -> Vec<u8> {
    frame(b'I', &[])
}

fn diagnostic(tag: u8, severity: &str, sqlstate: &str, message: &str) -> Vec<u8> {
    let mut b = Vec::new();
    for (field, value) in [
        (b'S', severity),
        (b'V', severity),
        (b'C', sqlstate),
        (b'M', message),
    ] {
        b.push(field);
        push_cstr(&mut b, value);
    }
    b.push(0);
    frame(tag, &b)
}

pub fn error_response(sqlstate: &str, message: &str) -> Vec<u8> {
    diagnostic(b'E', "ERROR", sqlstate, message)
}

pub fn fatal_response(sqlstate: &str, message: &str) -> Vec<u8> {
    diagnostic(b'E', "FATAL", sqlstate, message)
}

/// Carries the DP metadata of a result (`SPEC.md` §8): a `NOTICE` whose message is JSON.
pub fn notice_response(message: &str) -> Vec<u8> {
    diagnostic(b'N', "NOTICE", "00000", message)
}

/// Split one backend message into `(tag, body)`; for tests and the Rust test client.
pub fn split_backend(buf: &[u8]) -> Option<(u8, &[u8], &[u8])> {
    let tag = *buf.first()?;
    let len = i32::from_be_bytes(buf.get(1..5)?.try_into().ok()?) as usize;
    let body = buf.get(5..1 + len)?;
    Some((tag, body, &buf[1 + len..]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parts(msg: Vec<u8>) -> (u8, Vec<u8>) {
        let (tag, body, rest) = split_backend(&msg).unwrap();
        assert!(rest.is_empty());
        (tag, body.to_vec())
    }

    fn startup_packet(params: &[(&str, &str)]) -> Vec<u8> {
        let mut b = crate::PROTOCOL_VERSION.to_be_bytes().to_vec();
        for (k, v) in params {
            b.extend_from_slice(k.as_bytes());
            b.push(0);
            b.extend_from_slice(v.as_bytes());
            b.push(0);
        }
        b.push(0);
        b
    }

    #[test]
    fn startup_parameters_are_parsed() {
        let p = parse_startup(&startup_packet(&[
            ("user", "alice"),
            ("database", "safehealth"),
        ]))
        .unwrap();
        let Startup::Params(m) = p else { panic!() };
        assert_eq!(m["user"], "alice");
        assert_eq!(m["database"], "safehealth");
    }

    #[test]
    fn ssl_gss_and_cancel_requests_are_recognised() {
        assert_eq!(
            parse_startup(&SSL_REQUEST_CODE.to_be_bytes()).unwrap(),
            Startup::SslRequest
        );
        assert_eq!(
            parse_startup(&GSSENC_REQUEST_CODE.to_be_bytes()).unwrap(),
            Startup::GssEncRequest
        );
        let mut cancel = CANCEL_REQUEST_CODE.to_be_bytes().to_vec();
        cancel.extend_from_slice(&[0; 8]);
        assert_eq!(parse_startup(&cancel).unwrap(), Startup::Cancel);
    }

    #[test]
    fn malformed_startup_packets_are_parse_errors() {
        assert!(parse_startup(&[]).is_err());
        assert!(parse_startup(&[0, 0]).is_err());
        // Protocol 2.0 is not supported.
        assert!(parse_startup(&(2i32 << 16).to_be_bytes()).is_err());
        // Key with no terminator.
        let mut b = crate::PROTOCOL_VERSION.to_be_bytes().to_vec();
        b.extend_from_slice(b"user");
        assert!(parse_startup(&b).is_err());
    }

    #[test]
    fn frontend_messages_are_parsed() {
        assert_eq!(
            parse_frontend(b'Q', b"SELECT 1\0").unwrap(),
            Frontend::Query("SELECT 1".into())
        );
        assert_eq!(
            parse_frontend(b'p', b"secret\0").unwrap(),
            Frontend::Password("secret".into())
        );
        assert_eq!(parse_frontend(b'X', b"").unwrap(), Frontend::Terminate);
        // Parse/Bind/Execute are the extended protocol: not implemented.
        assert_eq!(
            parse_frontend(b'P', b"x").unwrap(),
            Frontend::Unsupported(b'P')
        );
        assert!(parse_frontend(b'Q', b"no terminator").is_err());
        assert!(parse_frontend(b'Q', &[0xFF, 0xFE, 0]).is_err());
    }

    #[test]
    fn backend_messages_have_the_right_framing() {
        let m = ready_for_query();
        assert_eq!(m, [b'Z', 0, 0, 0, 5, b'I']);
        assert_eq!(parts(auth_ok()), (b'R', vec![0, 0, 0, 0]));
        assert_eq!(parts(auth_cleartext_request()), (b'R', vec![0, 0, 0, 3]));
        assert_eq!(
            parts(command_complete("SELECT 2")),
            (b'C', b"SELECT 2\0".to_vec())
        );
    }

    #[test]
    fn row_description_and_data_row_encode_columns_and_nulls() {
        let desc = row_description(&[("value".into(), OID_INT8), ("g".into(), OID_TEXT)]);
        let (tag, body) = parts(desc);
        assert_eq!(tag, b'T');
        assert_eq!(&body[..2], &[0, 2]);
        assert!(body.windows(6).any(|w| w == b"value\0"));

        let row = data_row(&[Some("196".into()), None]);
        let (tag, body) = parts(row);
        assert_eq!(tag, b'D');
        assert_eq!(&body[..2], &[0, 2]);
        assert_eq!(&body[2..6], &3i32.to_be_bytes());
        assert_eq!(&body[6..9], b"196");
        assert_eq!(&body[9..13], &(-1i32).to_be_bytes());
    }

    #[test]
    fn errors_carry_severity_sqlstate_and_message() {
        let (tag, body) = parts(error_response("53400", "budget exhausted"));
        assert_eq!(tag, b'E');
        let text = String::from_utf8_lossy(&body);
        assert!(text.contains("SERROR"));
        assert!(text.contains("C53400"));
        assert!(text.contains("Mbudget exhausted"));
        assert_eq!(body.last(), Some(&0));
        let (tag, body) = parts(notice_response("{}"));
        assert_eq!(tag, b'N');
        assert!(String::from_utf8_lossy(&body).contains("SNOTICE"));
        assert!(String::from_utf8_lossy(&fatal_response("28000", "no")).contains("FATAL"));
    }

    #[test]
    fn an_embedded_nul_cannot_split_a_field() {
        let (_, body) = parts(error_response("XX000", "a\0b"));
        assert!(String::from_utf8_lossy(&body).contains("Mab\0"));
    }

    #[test]
    fn values_render_in_postgres_text_format() {
        assert_eq!(format_value(&Value::I64(-7)), Some("-7".into()));
        assert_eq!(format_value(&Value::F64(2.5)), Some("2.5".into()));
        assert_eq!(format_value(&Value::F64(f64::NAN)), Some("NaN".into()));
        assert_eq!(format_value(&Value::Bool(true)), Some("t".into()));
        assert_eq!(format_value(&Value::Null), None);
        assert_eq!(format_value(&Value::Text("x".into())), Some("x".into()));
    }

    #[test]
    fn timestamps_render_as_iso_text() {
        assert_eq!(format_timestamp(0), "1970-01-01 00:00:00");
        assert_eq!(
            format_timestamp(1_704_258_420_000_000),
            "2024-01-03 05:07:00"
        );
        assert_eq!(
            format_timestamp(951_782_400_500_000),
            "2000-02-29 00:00:00.500000"
        );
        assert_eq!(format_timestamp(-1_000_000), "1969-12-31 23:59:59");
    }

    #[test]
    fn type_oids() {
        assert_eq!(type_oid(ColumnType::I32), OID_INT8);
        assert_eq!(type_oid(ColumnType::F64), OID_FLOAT8);
        assert_eq!(type_oid(ColumnType::Text), OID_TEXT);
    }
}
