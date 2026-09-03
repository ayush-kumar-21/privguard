//! Column encryption and ledger hashing. `SPEC.md` §5.5 and §6.
//!
//! Encryption sits at the access-method layer, not above it: a column marked
//! encrypted is ciphertext on the page, so the bytes on disk never hold
//! plaintext even transiently.
//!
//! Landed in Phase 4.2 (`IMPLEMENTATION_PLAN.md`).

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
