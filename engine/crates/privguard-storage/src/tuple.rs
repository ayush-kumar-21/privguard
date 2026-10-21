//! Tuple encoding. `SPEC.md` §2.3.
//!
//! A tuple is a null bitmap followed by the non-null values in column order. The schema is
//! not stored with the tuple: decoding takes the column types from the catalog (or from the
//! hard-coded `sys_*` schemas while bootstrapping).

use privguard_core::{ColumnType, Error, Result, Row, Value};

fn bitmap_len(ncols: usize) -> usize {
    ncols.div_ceil(8)
}

/// Encode `row` against `types`. Errors if the arity or any value's type disagrees.
pub fn encode_row(types: &[ColumnType], row: &[Value]) -> Result<Vec<u8>> {
    if types.len() != row.len() {
        return Err(Error::engine(format!(
            "row has {} values, schema has {} columns",
            row.len(),
            types.len()
        )));
    }
    let mut out = vec![0u8; bitmap_len(types.len())];
    for (i, (ty, v)) in types.iter().zip(row).enumerate() {
        match (ty, v) {
            (_, Value::Null) => out[i / 8] |= 1 << (i % 8),
            (ColumnType::I32, Value::I32(x)) => out.extend_from_slice(&x.to_le_bytes()),
            (ColumnType::I64, Value::I64(x)) | (ColumnType::Timestamp, Value::Timestamp(x)) => {
                out.extend_from_slice(&x.to_le_bytes())
            }
            (ColumnType::F64, Value::F64(x)) => out.extend_from_slice(&x.to_le_bytes()),
            (ColumnType::Bool, Value::Bool(x)) => out.push(u8::from(*x)),
            (ColumnType::Text, Value::Text(s)) => {
                let len = u16::try_from(s.len())
                    .map_err(|_| Error::engine("text value longer than 65535 bytes"))?;
                out.extend_from_slice(&len.to_le_bytes());
                out.extend_from_slice(s.as_bytes());
            }
            (ty, v) => {
                return Err(Error::engine(format!(
                    "column {i} is {ty}, got value {v:?}"
                )))
            }
        }
    }
    Ok(out)
}

struct Reader<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        let end = self.pos + n;
        let s = self
            .buf
            .get(self.pos..end)
            .ok_or_else(|| Error::engine("truncated tuple"))?;
        self.pos = end;
        Ok(s)
    }
}

/// Decode a tuple written by [`encode_row`] with the same `types`.
pub fn decode_row(types: &[ColumnType], bytes: &[u8]) -> Result<Row> {
    let nb = bitmap_len(types.len());
    if bytes.len() < nb {
        return Err(Error::engine("truncated tuple"));
    }
    let mut r = Reader {
        buf: bytes,
        pos: nb,
    };
    let mut row = Vec::with_capacity(types.len());
    for (i, ty) in types.iter().enumerate() {
        if bytes[i / 8] & (1 << (i % 8)) != 0 {
            row.push(Value::Null);
            continue;
        }
        row.push(match ty {
            ColumnType::I32 => Value::I32(i32::from_le_bytes(r.take(4)?.try_into().unwrap())),
            ColumnType::I64 => Value::I64(i64::from_le_bytes(r.take(8)?.try_into().unwrap())),
            ColumnType::Timestamp => {
                Value::Timestamp(i64::from_le_bytes(r.take(8)?.try_into().unwrap()))
            }
            ColumnType::F64 => Value::F64(f64::from_le_bytes(r.take(8)?.try_into().unwrap())),
            ColumnType::Bool => Value::Bool(r.take(1)?[0] != 0),
            ColumnType::Text => {
                let len = u16::from_le_bytes(r.take(2)?.try_into().unwrap()) as usize;
                let s = std::str::from_utf8(r.take(len)?)
                    .map_err(|_| Error::engine("tuple text is not valid UTF-8"))?;
                Value::Text(s.to_string())
            }
        });
    }
    if r.pos != bytes.len() {
        return Err(Error::engine("trailing bytes after tuple"));
    }
    Ok(row)
}

#[cfg(test)]
mod tests {
    use super::*;

    const TYPES: [ColumnType; 6] = [
        ColumnType::I32,
        ColumnType::I64,
        ColumnType::F64,
        ColumnType::Text,
        ColumnType::Timestamp,
        ColumnType::Bool,
    ];

    #[test]
    fn round_trips_every_type() {
        let row = vec![
            Value::I32(-7),
            Value::I64(1 << 40),
            Value::F64(-2.5),
            Value::Text("héllo".into()),
            Value::Timestamp(1_700_000_000_000_000),
            Value::Bool(true),
        ];
        let bytes = encode_row(&TYPES, &row).unwrap();
        assert_eq!(decode_row(&TYPES, &bytes).unwrap(), row);
    }

    #[test]
    fn nulls_live_in_the_bitmap_and_take_no_space() {
        let row = vec![
            Value::Null,
            Value::I64(5),
            Value::Null,
            Value::Text(String::new()),
            Value::Null,
            Value::Bool(false),
        ];
        let bytes = encode_row(&TYPES, &row).unwrap();
        // 1 bitmap byte + i64 (8) + empty text (2) + bool (1)
        assert_eq!(bytes.len(), 1 + 8 + 2 + 1);
        assert_eq!(decode_row(&TYPES, &bytes).unwrap(), row);
    }

    #[test]
    fn more_than_eight_columns_use_a_wider_bitmap() {
        let types = vec![ColumnType::I32; 9];
        let mut row = vec![Value::I32(1); 9];
        row[8] = Value::Null;
        let bytes = encode_row(&types, &row).unwrap();
        assert_eq!(bytes.len(), 2 + 8 * 4);
        assert_eq!(decode_row(&types, &bytes).unwrap(), row);
    }

    #[test]
    fn type_and_arity_mismatches_are_engine_errors() {
        assert!(encode_row(&[ColumnType::I32], &[Value::Text("x".into())]).is_err());
        assert!(encode_row(&[ColumnType::I32], &[]).is_err());
    }

    #[test]
    fn truncated_and_trailing_bytes_are_rejected() {
        let bytes = encode_row(&[ColumnType::I64], &[Value::I64(1)]).unwrap();
        assert!(decode_row(&[ColumnType::I64], &bytes[..bytes.len() - 1]).is_err());
        let mut long = bytes.clone();
        long.push(0);
        assert!(decode_row(&[ColumnType::I64], &long).is_err());
    }
}
