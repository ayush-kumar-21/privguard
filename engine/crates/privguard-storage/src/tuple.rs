//! Tuple encoding. `SPEC.md` §2.3 and §5.5.
//!
//! A tuple is a null bitmap followed by the non-null values in column order. The schema is
//! not stored with the tuple: decoding takes the column types from the catalog (or from the
//! hard-coded `sys_*` schemas while bootstrapping).
//!
//! Columns marked encrypted in the catalog go through a [`ValueCipher`] here, at the
//! access-method layer, so the bytes on a page never hold their plaintext.

use privguard_core::{ColumnType, Error, Result, Row, Value};

/// Seals and opens the bytes of selected columns (`SPEC.md` §5.5). Implemented by
/// `privguard-crypto`; defined here so storage does not depend on it.
pub trait ValueCipher {
    /// Whether column `col` is stored encrypted.
    fn encrypts(&self, col: usize) -> bool;
    fn seal(&self, col: usize, plaintext: &[u8]) -> Result<Vec<u8>>;
    fn open(&self, col: usize, stored: &[u8]) -> Result<Vec<u8>>;
}

fn bitmap_len(ncols: usize) -> usize {
    ncols.div_ceil(8)
}

/// Serialise one non-null value.
fn put_value(i: usize, ty: ColumnType, v: &Value, out: &mut Vec<u8>) -> Result<()> {
    match (ty, v) {
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
    Ok(())
}

/// Encode `row` against `types`. Errors if the arity or any value's type disagrees.
pub fn encode_row(types: &[ColumnType], row: &[Value]) -> Result<Vec<u8>> {
    encode_row_with(types, row, None)
}

/// Like [`encode_row`], sealing the columns `cipher` says are encrypted. A sealed value is stored
/// as `u16 length | sealed bytes`. `NULL` stays in the bitmap, so nullness is not hidden.
pub fn encode_row_with(
    types: &[ColumnType],
    row: &[Value],
    cipher: Option<&dyn ValueCipher>,
) -> Result<Vec<u8>> {
    if types.len() != row.len() {
        return Err(Error::engine(format!(
            "row has {} values, schema has {} columns",
            row.len(),
            types.len()
        )));
    }
    let mut out = vec![0u8; bitmap_len(types.len())];
    for (i, (ty, v)) in types.iter().zip(row).enumerate() {
        if v.is_null() {
            out[i / 8] |= 1 << (i % 8);
            continue;
        }
        match cipher.filter(|c| c.encrypts(i)) {
            Some(c) => {
                let mut plain = Vec::new();
                put_value(i, *ty, v, &mut plain)?;
                let sealed = c.seal(i, &plain)?;
                let len = u16::try_from(sealed.len())
                    .map_err(|_| Error::engine("encrypted value longer than 65535 bytes"))?;
                out.extend_from_slice(&len.to_le_bytes());
                out.extend_from_slice(&sealed);
            }
            None => put_value(i, *ty, v, &mut out)?,
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

    fn value(&mut self, ty: ColumnType) -> Result<Value> {
        Ok(match ty {
            ColumnType::I32 => Value::I32(i32::from_le_bytes(self.take(4)?.try_into().unwrap())),
            ColumnType::I64 => Value::I64(i64::from_le_bytes(self.take(8)?.try_into().unwrap())),
            ColumnType::Timestamp => {
                Value::Timestamp(i64::from_le_bytes(self.take(8)?.try_into().unwrap()))
            }
            ColumnType::F64 => Value::F64(f64::from_le_bytes(self.take(8)?.try_into().unwrap())),
            ColumnType::Bool => Value::Bool(self.take(1)?[0] != 0),
            ColumnType::Text => {
                let len = u16::from_le_bytes(self.take(2)?.try_into().unwrap()) as usize;
                let s = std::str::from_utf8(self.take(len)?)
                    .map_err(|_| Error::engine("tuple text is not valid UTF-8"))?;
                Value::Text(s.to_string())
            }
        })
    }
}

/// Decode a tuple written by [`encode_row`] with the same `types`.
pub fn decode_row(types: &[ColumnType], bytes: &[u8]) -> Result<Row> {
    decode_row_with(types, bytes, None)
}

/// Decode a tuple written by [`encode_row_with`] with the same `types` and cipher.
pub fn decode_row_with(
    types: &[ColumnType],
    bytes: &[u8],
    cipher: Option<&dyn ValueCipher>,
) -> Result<Row> {
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
        match cipher.filter(|c| c.encrypts(i)) {
            Some(c) => {
                let len = u16::from_le_bytes(r.take(2)?.try_into().unwrap()) as usize;
                let plain = c.open(i, r.take(len)?)?;
                let mut inner = Reader {
                    buf: &plain,
                    pos: 0,
                };
                let v = inner.value(*ty)?;
                if inner.pos != plain.len() {
                    return Err(Error::engine("trailing bytes in a decrypted value"));
                }
                row.push(v);
            }
            None => row.push(r.value(*ty)?),
        }
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

    /// Test cipher: XOR with a column-dependent byte and a tag, enough to prove the plumbing.
    struct Xor {
        encrypted: Vec<bool>,
    }

    impl ValueCipher for Xor {
        fn encrypts(&self, col: usize) -> bool {
            self.encrypted[col]
        }
        fn seal(&self, col: usize, plaintext: &[u8]) -> Result<Vec<u8>> {
            let mut out: Vec<u8> = plaintext.iter().map(|b| b ^ (0x5A + col as u8)).collect();
            out.push(0xEE);
            Ok(out)
        }
        fn open(&self, col: usize, stored: &[u8]) -> Result<Vec<u8>> {
            match stored.split_last() {
                Some((0xEE, body)) => Ok(body.iter().map(|b| b ^ (0x5A + col as u8)).collect()),
                _ => Err(Error::engine("bad seal")),
            }
        }
    }

    #[test]
    fn sealed_columns_round_trip_and_are_not_plaintext_on_the_page() {
        let c = Xor {
            encrypted: vec![false, false, false, true, false, true],
        };
        let row = vec![
            Value::I32(1),
            Value::I64(2),
            Value::F64(3.0),
            Value::Text("MRN-000042".into()),
            Value::Timestamp(4),
            Value::Bool(true),
        ];
        let bytes = encode_row_with(&TYPES, &row, Some(&c)).unwrap();
        assert!(
            !bytes.windows(10).any(|w| w == b"MRN-000042"),
            "plaintext leaked"
        );
        assert_eq!(decode_row_with(&TYPES, &bytes, Some(&c)).unwrap(), row);
        // The plain columns are still readable without the cipher; the sealed ones are not.
        assert!(decode_row(&TYPES, &bytes).is_err());
    }

    #[test]
    fn null_sealed_columns_stay_null_and_the_rest_still_decodes() {
        let c = Xor {
            encrypted: vec![false, false, false, true, false, false],
        };
        let row = vec![
            Value::I32(1),
            Value::I64(2),
            Value::F64(3.0),
            Value::Null,
            Value::Timestamp(4),
            Value::Bool(false),
        ];
        let bytes = encode_row_with(&TYPES, &row, Some(&c)).unwrap();
        assert_eq!(decode_row_with(&TYPES, &bytes, Some(&c)).unwrap(), row);
    }

    #[test]
    fn a_tampered_sealed_value_fails_to_decode() {
        let c = Xor {
            encrypted: vec![true],
        };
        let mut bytes = encode_row_with(&[ColumnType::I64], &[Value::I64(9)], Some(&c)).unwrap();
        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        assert!(decode_row_with(&[ColumnType::I64], &bytes, Some(&c)).is_err());
    }
}
