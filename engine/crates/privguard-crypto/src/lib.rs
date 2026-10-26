//! Column encryption and ledger hashing. `SPEC.md` §5.5 and §6.
//!
//! Encryption sits at the access-method layer, not above it: a column marked
//! encrypted is ciphertext on the page, so the bytes on disk never hold
//! plaintext even transiently.
//!
//! Landed in Phase 4.2 (`IMPLEMENTATION_PLAN.md`).

use aes_gcm::aead::{Aead, Generate, KeyInit, Payload};
use aes_gcm::aead::{Key, Nonce};
use aes_gcm::Aes256Gcm;
use privguard_core::{Error, Result};
use privguard_storage::ValueCipher;
use std::fmt;

/// AES-256-GCM parameters, frozen in `SPEC.md` §5.5.
pub mod aes {
    /// 256-bit key.
    pub const KEY_LEN: usize = 32;
    /// 96-bit nonce — the size GCM is defined for; anything else forces an
    /// extra derivation step and weakens the birthday bound.
    pub const NONCE_LEN: usize = 12;
    /// 128-bit authentication tag, appended to the ciphertext.
    pub const TAG_LEN: usize = 16;

    /// Stored size of an encrypted value: `nonce ‖ ciphertext ‖ tag`.
    ///
    /// Every encrypted value carries its own fresh nonce, which is why this
    /// overhead is per value rather than per page.
    pub const fn stored_len(plaintext_len: usize) -> usize {
        NONCE_LEN + plaintext_len + TAG_LEN
    }
}

/// Length of a blake3 digest, used for the budget ledger's hash chain.
pub const LEDGER_HASH_LEN: usize = 32;

/// The chain's genesis hash — all zeroes. The first ledger entry for an account
/// uses this as its `prev_hash`, so verification has a defined starting point.
pub const GENESIS_HASH: [u8; LEDGER_HASH_LEN] = [0u8; LEDGER_HASH_LEN];

/// The 256-bit key, held by the engine process only. `SPEC.md` §5.5: it comes from the keyring
/// (here: an environment variable, `PRIVGUARD_COLUMN_KEY`, 64 hex digits), never from the catalog.
#[derive(Clone)]
pub struct Keyring {
    key: [u8; aes::KEY_LEN],
}

impl Keyring {
    pub fn from_bytes(key: [u8; aes::KEY_LEN]) -> Self {
        Keyring { key }
    }

    /// Parse 64 hex digits.
    pub fn from_hex(hex: &str) -> Result<Self> {
        let hex = hex.trim();
        if hex.len() != aes::KEY_LEN * 2 || !hex.is_ascii() {
            return Err(Error::policy(format!(
                "the column key must be {} hex digits",
                aes::KEY_LEN * 2
            )));
        }
        let mut key = [0u8; aes::KEY_LEN];
        for (i, slot) in key.iter_mut().enumerate() {
            *slot = u8::from_str_radix(&hex[2 * i..2 * i + 2], 16)
                .map_err(|_| Error::policy("the column key must be hexadecimal"))?;
        }
        Ok(Keyring { key })
    }

    /// Read the key from `PRIVGUARD_COLUMN_KEY`; `None` if it is not set.
    pub fn from_env() -> Result<Option<Self>> {
        match std::env::var("PRIVGUARD_COLUMN_KEY") {
            Ok(v) => Self::from_hex(&v).map(Some),
            Err(_) => Ok(None),
        }
    }

    /// A cipher for the columns of `table` named in `encrypted` (by position, `None` = plaintext).
    pub fn table_cipher(&self, table: &str, columns: &[Option<String>]) -> TableCipher {
        TableCipher {
            aead: Aes256Gcm::new(&Key::<Aes256Gcm>::from(self.key)),
            table: table.to_string(),
            columns: columns.to_vec(),
        }
    }
}

// A key must never reach a log line.
impl fmt::Debug for Keyring {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Keyring([redacted])")
    }
}

/// Seals and opens the encrypted columns of one table.
///
/// Every value gets a fresh random 96-bit nonce, stored in front of the ciphertext, with the
/// 128-bit tag appended: `nonce ‖ ciphertext ‖ tag` (`aes::stored_len`). The associated data is
/// `table.column`, so a ciphertext copied into a different column fails authentication instead of
/// decrypting.
pub struct TableCipher {
    aead: Aes256Gcm,
    table: String,
    /// Column name for each position that is encrypted.
    columns: Vec<Option<String>>,
}

impl TableCipher {
    fn aad(&self, col: usize) -> Result<Vec<u8>> {
        let name = self
            .columns
            .get(col)
            .and_then(|c| c.as_ref())
            .ok_or_else(|| Error::engine(format!("column {col} is not encrypted")))?;
        Ok(format!("{}.{name}", self.table).into_bytes())
    }

    /// Whether any column of the table is encrypted.
    pub fn any(&self) -> bool {
        self.columns.iter().any(Option::is_some)
    }
}

impl ValueCipher for TableCipher {
    fn encrypts(&self, col: usize) -> bool {
        self.columns.get(col).is_some_and(Option::is_some)
    }

    fn seal(&self, col: usize, plaintext: &[u8]) -> Result<Vec<u8>> {
        let aad = self.aad(col)?;
        let nonce = Nonce::<Aes256Gcm>::generate(); // 96-bit nonce from the OS CSPRNG (getrandom)
        let ct = self
            .aead
            .encrypt(
                &nonce,
                Payload {
                    msg: plaintext,
                    aad: &aad,
                },
            )
            .map_err(|_| Error::engine("encryption failed"))?;
        let mut out = Vec::with_capacity(aes::stored_len(plaintext.len()));
        out.extend_from_slice(&nonce);
        out.extend_from_slice(&ct);
        Ok(out)
    }

    fn open(&self, col: usize, stored: &[u8]) -> Result<Vec<u8>> {
        let aad = self.aad(col)?;
        if stored.len() < aes::stored_len(0) {
            return Err(Error::engine("encrypted value is too short"));
        }
        let (nonce, ct) = stored.split_at(aes::NONCE_LEN);
        let nonce = Nonce::<Aes256Gcm>::try_from(nonce)
            .map_err(|_| Error::engine("encrypted value has a malformed nonce"))?;
        self.aead
            .decrypt(&nonce, Payload { msg: ct, aad: &aad })
            // Wrong key, wrong column, or a flipped bit all look the same, by design.
            .map_err(|_| Error::engine("encrypted value failed authentication"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aes_parameters_match_spec() {
        assert_eq!(aes::KEY_LEN, 32, "AES-256 needs a 256-bit key");
        assert_eq!(aes::NONCE_LEN, 12, "GCM is defined for a 96-bit nonce");
        assert_eq!(aes::TAG_LEN, 16);
    }

    #[test]
    fn encryption_overhead_is_constant_per_value() {
        assert_eq!(aes::stored_len(0), 28);
        assert_eq!(aes::stored_len(8), 36);
        // Overhead does not grow with the plaintext.
        assert_eq!(aes::stored_len(1000) - 1000, aes::stored_len(0));
    }

    #[test]
    fn an_encrypted_value_still_fits_a_page() {
        use privguard_core::PAGE_SIZE;
        assert!(aes::stored_len(64) < PAGE_SIZE);
    }

    #[test]
    fn genesis_hash_is_all_zeroes() {
        assert_eq!(GENESIS_HASH, [0u8; 32]);
        assert_eq!(GENESIS_HASH.len(), LEDGER_HASH_LEN);
    }
}

#[cfg(test)]
mod cipher_tests {
    use super::*;
    use privguard_core::{ColumnType, Value};
    use privguard_storage::{decode_row_with, encode_row_with};

    const KEY: &str = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";

    fn cipher(columns: &[Option<&str>]) -> TableCipher {
        let cols: Vec<_> = columns.iter().map(|c| c.map(str::to_string)).collect();
        Keyring::from_hex(KEY)
            .unwrap()
            .table_cipher("patients", &cols)
    }

    #[test]
    fn a_sealed_value_opens_to_the_original() {
        let c = cipher(&[Some("mrn")]);
        let sealed = c.seal(0, b"MRN000042").unwrap();
        assert_eq!(sealed.len(), aes::stored_len(9));
        assert_eq!(c.open(0, &sealed).unwrap(), b"MRN000042");
    }

    #[test]
    fn every_seal_uses_a_fresh_nonce() {
        let c = cipher(&[Some("mrn")]);
        let (a, b) = (c.seal(0, b"same").unwrap(), c.seal(0, b"same").unwrap());
        assert_ne!(a, b, "equal plaintexts must not give equal ciphertexts");
        assert_ne!(a[..aes::NONCE_LEN], b[..aes::NONCE_LEN]);
        assert_eq!(c.open(0, &a).unwrap(), c.open(0, &b).unwrap());
    }

    #[test]
    fn a_flipped_bit_anywhere_is_detected() {
        let c = cipher(&[Some("mrn")]);
        let sealed = c.seal(0, b"MRN000042").unwrap();
        for i in 0..sealed.len() {
            let mut bad = sealed.clone();
            bad[i] ^= 0x01;
            assert!(c.open(0, &bad).is_err(), "byte {i}");
        }
        assert!(c.open(0, &sealed[..sealed.len() - 1]).is_err());
        assert!(c.open(0, &[]).is_err());
    }

    #[test]
    fn a_different_key_cannot_open_it() {
        let c = cipher(&[Some("mrn")]);
        let sealed = c.seal(0, b"secret").unwrap();
        let other = Keyring::from_bytes([9u8; 32]).table_cipher("patients", &[Some("mrn".into())]);
        assert!(other.open(0, &sealed).is_err());
    }

    #[test]
    fn a_ciphertext_cannot_be_moved_to_another_column_or_table() {
        let two = cipher(&[Some("mrn"), Some("ssn")]);
        let sealed = two.seal(0, b"value").unwrap();
        assert!(two.open(1, &sealed).is_err(), "same table, other column");
        let other_table = Keyring::from_hex(KEY)
            .unwrap()
            .table_cipher("staff", &[Some("mrn".into())]);
        assert!(other_table.open(0, &sealed).is_err(), "other table");
    }

    #[test]
    fn plaintext_columns_are_refused_by_the_cipher() {
        let c = cipher(&[None, Some("mrn")]);
        assert!(!c.encrypts(0) && c.encrypts(1) && !c.encrypts(7));
        assert!(c.seal(0, b"x").is_err());
        assert!(c.any());
        assert!(!cipher(&[None]).any());
    }

    #[test]
    fn keys_are_parsed_strictly_and_never_printed() {
        assert!(Keyring::from_hex(KEY).is_ok());
        for bad in [
            "",
            "abcd",
            &KEY[..62],
            &format!("{KEY}00"),
            &KEY.replace('0', "g"),
        ] {
            assert!(Keyring::from_hex(bad).is_err(), "{bad}");
        }
        assert_eq!(
            format!("{:?}", Keyring::from_hex(KEY).unwrap()),
            "Keyring([redacted])"
        );
    }

    #[test]
    fn a_tuple_with_an_encrypted_column_round_trips_and_hides_the_plaintext() {
        let c = cipher(&[None, Some("mrn")]);
        let types = [ColumnType::I64, ColumnType::Text];
        let row = vec![Value::I64(7), Value::Text("MRN000042".into())];
        let bytes = encode_row_with(&types, &row, Some(&c)).unwrap();
        assert!(!bytes.windows(9).any(|w| w == b"MRN000042"));
        assert_eq!(decode_row_with(&types, &bytes, Some(&c)).unwrap(), row);
    }
}
