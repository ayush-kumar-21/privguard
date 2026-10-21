//! A small JSON reader/writer.
//!
//! The engine needs JSON in three places and has no JSON dependency: reading the oracle's
//! golden fixtures, storing cached answers in `sys_noise_cache`, and the audit log. This covers
//! exactly that: objects keep insertion order, numbers remember whether they were written as
//! integers (the fixtures distinguish `196` from `196.0`), and `\uXXXX` escapes are supported.

use crate::{Error, Result, Value};
use std::fmt::Write;

#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    /// The value, and whether the literal had no fraction or exponent.
    Num(f64, bool),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    pub fn int(n: i64) -> Json {
        Json::Num(n as f64, true)
    }

    pub fn float(x: f64) -> Json {
        Json::Num(x, false)
    }

    pub fn str(s: impl Into<String>) -> Json {
        Json::Str(s.into())
    }

    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(kv) => kv.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Json]> {
        match self {
            Json::Arr(v) => Some(v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Json::Num(n, _) => Some(*n),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Json::Num(n, true) => Some(*n as i64),
            _ => None,
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Json::Null)
    }

    /// A scalar as an engine [`Value`]: integers become `I64`, other numbers `F64`.
    pub fn to_value(&self) -> Option<Value> {
        Some(match self {
            Json::Null => Value::Null,
            Json::Bool(b) => Value::Bool(*b),
            Json::Num(n, true) => Value::I64(*n as i64),
            Json::Num(n, false) => Value::F64(*n),
            Json::Str(s) => Value::Text(s.clone()),
            _ => return None,
        })
    }

    pub fn from_value(v: &Value) -> Json {
        match v {
            Value::Null => Json::Null,
            Value::Bool(b) => Json::Bool(*b),
            Value::I32(x) => Json::int(i64::from(*x)),
            Value::I64(x) | Value::Timestamp(x) => Json::int(*x),
            Value::F64(x) => Json::float(*x),
            Value::Text(s) => Json::Str(s.clone()),
        }
    }

    /// Compact serialisation. Non-finite floats become `null` (JSON has no spelling for them).
    pub fn to_json_string(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }

    fn write(&self, out: &mut String) {
        match self {
            Json::Null => out.push_str("null"),
            Json::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Json::Num(n, _) if !n.is_finite() => out.push_str("null"),
            Json::Num(n, true) => {
                let _ = write!(out, "{}", *n as i64);
            }
            // `{:?}` keeps a trailing `.0`, so a float round-trips as a float.
            Json::Num(n, false) => {
                let _ = write!(out, "{n:?}");
            }
            Json::Str(s) => write_string(s, out),
            Json::Arr(v) => {
                out.push('[');
                for (i, x) in v.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    x.write(out);
                }
                out.push(']');
            }
            Json::Obj(kv) => {
                out.push('{');
                for (i, (k, v)) in kv.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    write_string(k, out);
                    out.push(':');
                    v.write(out);
                }
                out.push('}');
            }
        }
    }
}

fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

struct Parser<'a> {
    b: &'a [u8],
    i: usize,
}

impl Parser<'_> {
    fn err<T>(&self, what: &str) -> Result<T> {
        Err(Error::engine(format!(
            "invalid JSON at byte {}: {what}",
            self.i
        )))
    }

    fn ws(&mut self) {
        while self.i < self.b.len() && self.b[self.i].is_ascii_whitespace() {
            self.i += 1;
        }
    }

    fn eat(&mut self, s: &str) -> bool {
        self.ws();
        if self.b[self.i..].starts_with(s.as_bytes()) {
            self.i += s.len();
            true
        } else {
            false
        }
    }

    fn hex4(&mut self) -> Result<u32> {
        let h = self
            .b
            .get(self.i..self.i + 4)
            .and_then(|h| std::str::from_utf8(h).ok())
            .and_then(|h| u32::from_str_radix(h, 16).ok());
        match h {
            Some(v) => {
                self.i += 4;
                Ok(v)
            }
            None => self.err("bad \\u escape"),
        }
    }

    fn string(&mut self) -> Result<String> {
        if !self.eat("\"") {
            return self.err("expected a string");
        }
        let mut out: Vec<u8> = Vec::new();
        loop {
            let Some(&c) = self.b.get(self.i) else {
                return self.err("unterminated string");
            };
            self.i += 1;
            match c {
                b'"' => return String::from_utf8(out).or_else(|_| self.err("string is not UTF-8")),
                b'\\' => {
                    let Some(&e) = self.b.get(self.i) else {
                        return self.err("unterminated escape");
                    };
                    self.i += 1;
                    match e {
                        b'n' => out.push(b'\n'),
                        b't' => out.push(b'\t'),
                        b'r' => out.push(b'\r'),
                        b'b' => out.push(8),
                        b'f' => out.push(12),
                        b'u' => {
                            let mut cp = self.hex4()?;
                            if (0xD800..0xDC00).contains(&cp)
                                && self.b[self.i..].starts_with(b"\\u")
                            {
                                self.i += 2;
                                let lo = self.hex4()?;
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo.wrapping_sub(0xDC00));
                            }
                            let ch = char::from_u32(cp).unwrap_or('\u{FFFD}');
                            out.extend_from_slice(ch.to_string().as_bytes());
                        }
                        other => out.push(other),
                    }
                }
                c => out.push(c),
            }
        }
    }

    fn value(&mut self, depth: usize) -> Result<Json> {
        if depth > 64 {
            return self.err("nesting too deep");
        }
        self.ws();
        if self.eat("null") {
            return Ok(Json::Null);
        }
        if self.eat("true") {
            return Ok(Json::Bool(true));
        }
        if self.eat("false") {
            return Ok(Json::Bool(false));
        }
        match self.b.get(self.i) {
            None => self.err("unexpected end"),
            Some(b'"') => Ok(Json::Str(self.string()?)),
            Some(b'[') => {
                self.i += 1;
                let mut v = Vec::new();
                if !self.eat("]") {
                    loop {
                        v.push(self.value(depth + 1)?);
                        if self.eat("]") {
                            break;
                        }
                        if !self.eat(",") {
                            return self.err("expected , or ]");
                        }
                    }
                }
                Ok(Json::Arr(v))
            }
            Some(b'{') => {
                self.i += 1;
                let mut v = Vec::new();
                if !self.eat("}") {
                    loop {
                        self.ws();
                        let k = self.string()?;
                        if !self.eat(":") {
                            return self.err("expected :");
                        }
                        v.push((k, self.value(depth + 1)?));
                        if self.eat("}") {
                            break;
                        }
                        if !self.eat(",") {
                            return self.err("expected , or }");
                        }
                    }
                }
                Ok(Json::Obj(v))
            }
            Some(_) => {
                let start = self.i;
                while self.i < self.b.len() && !b",]} \n\r\t".contains(&self.b[self.i]) {
                    self.i += 1;
                }
                let text = std::str::from_utf8(&self.b[start..self.i]).unwrap_or("");
                match text.parse::<f64>() {
                    Ok(n) => Ok(Json::Num(n, !text.contains(['.', 'e', 'E']))),
                    Err(_) => self.err("bad number"),
                }
            }
        }
    }
}

/// Parse one JSON document; trailing non-whitespace is an error.
pub fn parse(text: &str) -> Result<Json> {
    let mut p = Parser {
        b: text.as_bytes(),
        i: 0,
    };
    let v = p.value(0)?;
    p.ws();
    if p.i != p.b.len() {
        return p.err("trailing characters");
    }
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nested_documents_and_keeps_object_order() {
        let j = parse(r#" {"b": [1, 2.5, "x"], "a": {"n": null, "t": true}} "#).unwrap();
        let Json::Obj(kv) = &j else { panic!() };
        assert_eq!(kv[0].0, "b");
        assert_eq!(kv[1].0, "a");
        assert_eq!(j.get("b").unwrap().as_array().unwrap().len(), 3);
        assert!(j.get("a").unwrap().get("n").unwrap().is_null());
        assert_eq!(j.get("zzz"), None);
    }

    #[test]
    fn integers_and_floats_stay_distinct() {
        assert_eq!(parse("196").unwrap().to_value(), Some(Value::I64(196)));
        assert_eq!(parse("196.0").unwrap().to_value(), Some(Value::F64(196.0)));
        assert_eq!(parse("1e-06").unwrap().to_value(), Some(Value::F64(1e-6)));
        assert_eq!(parse("-5").unwrap().as_i64(), Some(-5));
        assert_eq!(parse("5.0").unwrap().as_i64(), None);
    }

    #[test]
    fn strings_round_trip_with_escapes() {
        let s = "quote \" slash \\ nl \n tab \t ctl \u{1} é \u{1F600}";
        let text = Json::str(s).to_json_string();
        assert_eq!(parse(&text).unwrap(), Json::str(s));
        assert_eq!(parse(r#""é😀""#).unwrap(), Json::str("é\u{1F600}"));
    }

    #[test]
    fn serialisation_round_trips() {
        let j = Json::Obj(vec![
            (
                "rows".into(),
                Json::Arr(vec![Json::int(1), Json::float(2.0), Json::Null]),
            ),
            ("ok".into(), Json::Bool(false)),
            ("name".into(), Json::str("a\"b")),
        ]);
        let text = j.to_json_string();
        assert_eq!(text, r#"{"rows":[1,2.0,null],"ok":false,"name":"a\"b"}"#);
        assert_eq!(parse(&text).unwrap(), j);
    }

    #[test]
    fn non_finite_floats_serialise_as_null() {
        assert_eq!(Json::float(f64::NAN).to_json_string(), "null");
        assert_eq!(Json::float(f64::INFINITY).to_json_string(), "null");
    }

    #[test]
    fn malformed_documents_are_errors() {
        for bad in [
            "",
            "{",
            "[1,",
            r#"{"a" 1}"#,
            "tru",
            r#""abc"#,
            "1 2",
            "[1 2]",
            "{\"a\":}",
        ] {
            assert!(parse(bad).is_err(), "{bad:?}");
        }
        let deep = "[".repeat(100) + &"]".repeat(100);
        assert!(parse(&deep).is_err());
    }

    #[test]
    fn engine_values_map_both_ways() {
        for v in [
            Value::Null,
            Value::Bool(true),
            Value::I64(7),
            Value::F64(0.5),
            Value::Text("t".into()),
        ] {
            assert_eq!(Json::from_value(&v).to_value(), Some(v));
        }
        assert_eq!(Json::from_value(&Value::I32(3)), Json::int(3));
        assert_eq!(Json::Arr(vec![]).to_value(), None);
    }
}
