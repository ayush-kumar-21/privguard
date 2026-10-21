//! Keyed deterministic noise stream and DP mechanisms. `SPEC.md` §4.4 (`IMPLEMENTATION_PLAN.md` 3.2).
//!
//! Mirrors `oracle/privguard_oracle/mechanisms.py` bit-for-bit:
//! - `seed = SHA-256(server_secret || ast_hash || principal_id_be64 || salt)`
//! - Uniform stream is `SHA-256(seed || counter_be64)` in counter mode, consumed 8 bytes
//!   at a time, taking the top 53 bits in `[0, 1)`.
//! - `laplace`: inverse CDF.
//! - `gaussian`: Box–Muller with `u1` floored at `1e-12`.
//! - `discrete_laplace`: `laplace(scale, rng).round_ties_even() as i64`.

const SHA256_K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/// Compute FIPS 180-4 SHA-256 over `data`.
pub fn sha256(data: &[u8]) -> [u8; 32] {
    let mut state: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];

    let bit_len = (data.len() as u64) * 8;
    let mut padded = Vec::with_capacity((data.len() + 9).div_ceil(64) * 64);
    padded.extend_from_slice(data);
    padded.push(0x80);
    while (padded.len() % 64) != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_len.to_be_bytes());

    for chunk in padded.chunks_exact(64) {
        let mut w = [0u32; 64];
        for (i, word) in chunk.chunks_exact(4).enumerate() {
            w[i] = u32::from_be_bytes(word.try_into().unwrap());
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = state;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(SHA256_K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);

            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }

        for (s, v) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *s = s.wrapping_add(v);
        }
    }

    let mut out = [0u8; 32];
    for (i, word) in state.iter().enumerate() {
        out[4 * i..4 * i + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

/// Derive the per-query noise seed (`SPEC.md` §4.4):
/// `SHA-256(server_secret || ast_hash || principal_id_be64 || salt)`.
pub fn derive_seed(
    server_secret: &[u8],
    ast_hash: &[u8; 32],
    principal_id: u64,
    salt: &[u8],
) -> [u8; 32] {
    let mut input = Vec::with_capacity(server_secret.len() + 32 + 8 + salt.len());
    input.extend_from_slice(server_secret);
    input.extend_from_slice(ast_hash);
    input.extend_from_slice(&principal_id.to_be_bytes());
    input.extend_from_slice(salt);
    sha256(&input)
}

/// Deterministic uniform `[0, 1)` stream over `SHA-256(seed || counter_be64)`.
#[derive(Debug, Clone)]
pub struct KeyedRandom {
    seed: Vec<u8>,
    counter: u64,
    buf: [u8; 32],
    pos: usize,
}

impl KeyedRandom {
    pub fn new(seed: &[u8]) -> Self {
        KeyedRandom {
            seed: seed.to_vec(),
            counter: 0,
            buf: [0u8; 32],
            pos: 32,
        }
    }

    fn refill(&mut self) {
        let mut msg = Vec::with_capacity(self.seed.len() + 8);
        msg.extend_from_slice(&self.seed);
        msg.extend_from_slice(&self.counter.to_be_bytes());
        self.buf = sha256(&msg);
        self.counter += 1;
        self.pos = 0;
    }

    /// 53-bit uniform float in `[0, 1)`, identical to `KeyedRandom.uniform` in the oracle.
    pub fn uniform(&mut self) -> f64 {
        if self.pos + 8 > 32 {
            self.refill();
        }
        let chunk = u64::from_be_bytes(self.buf[self.pos..self.pos + 8].try_into().unwrap());
        self.pos += 8;
        ((chunk >> 11) as f64) / ((1u64 << 53) as f64)
    }
}

/// Sample `Laplace(0, scale)` by inverse CDF (`SPEC.md` §4.4).
pub fn laplace(scale: f64, rng: &mut KeyedRandom) -> f64 {
    if scale <= 0.0 {
        return 0.0;
    }
    let u = rng.uniform() - 0.5;
    if u == 0.0 {
        return 0.0;
    }
    -scale * (1.0f64).copysign(u) * (1.0 - 2.0 * u.abs()).ln()
}

/// Sample `N(0, sigma^2)` by Box–Muller (`SPEC.md` §4.4).
pub fn gaussian(sigma: f64, rng: &mut KeyedRandom) -> f64 {
    if sigma <= 0.0 {
        return 0.0;
    }
    let u1 = rng.uniform().max(1e-12);
    let u2 = rng.uniform();
    let z = (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos();
    sigma * z
}

/// Integer-valued Laplace noise for counts (`round_ties_even`, matching Python `round`).
pub fn discrete_laplace(scale: f64, rng: &mut KeyedRandom) -> i64 {
    let x = laplace(scale, rng);
    let r = x.round();
    if (x - r).abs() == 0.5 && (r as i64) % 2 != 0 {
        x.trunc() as i64
    } else {
        r as i64
    }
}

/// Clamp `value` into `[lo, hi]`, treating `None` as unbounded.
pub fn clamp(mut value: f64, lo: Option<f64>, hi: Option<f64>) -> f64 {
    if let Some(l) = lo {
        value = value.max(l);
    }
    if let Some(h) = hi {
        value = value.min(h);
    }
    value
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::accounting::{grouped_l2_sensitivity, laplace_scale_from_rho, sigma_from_rho};
    use crate::{CanonicalQuery, QueryFingerprint};

    fn canonical_query(agg: &str, col: Option<&str>, group: Option<&str>) -> CanonicalQuery {
        CanonicalQuery {
            agg: agg.into(),
            column: col.map(Into::into),
            table: "visits".into(),
            predicates: vec![],
            group_by: group.map(Into::into),
        }
    }

    #[test]
    fn sha256_matches_nist_vectors() {
        let empty = sha256(b"");
        assert_eq!(
            QueryFingerprint::from_digest(empty).to_hex(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        let abc = sha256(b"abc");
        assert_eq!(
            QueryFingerprint::from_digest(abc).to_hex(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn uniform_laplace_gaussian_and_discrete_laplace_match_oracle_bit_for_bit() {
        let secret = b"privguard-golden-secret-v1";
        let fp = canonical_query("COUNT", None, None).fingerprint().unwrap();
        assert_eq!(
            fp.to_hex(),
            "edc6f9946aed8757f617dac4a6ea3c1120f72cbaac6267577007b2db661238a5"
        );
        let seed = derive_seed(secret, fp.as_bytes(), 1, b"");
        assert_eq!(
            QueryFingerprint::from_digest(seed).to_hex(),
            "fea9e31a56a25426bc55ac900511eae055d1f70424a000c9170d98f7c7559b4d"
        );

        let mut rng = KeyedRandom::new(&seed);
        let want_uniform_bits: [u64; 6] = [
            0x3fe0743973e17b08,
            0x3fe43df9f3d3c142,
            0x3fee4f76332f3368,
            0x3fdcd5da076a54d4,
            0x3fe563b8e52d7284,
            0x3fc095c2729d2168,
        ];
        for want in want_uniform_bits {
            assert_eq!(rng.uniform().to_bits(), want);
        }

        let mut rng = KeyedRandom::new(&seed);
        let want_laplace_bits: [u64; 6] = [
            0x3fc26c359eac7067,
            0x3ff8a52122bbbba4,
            0x40267b1fc1c2daa0,
            0xbfe0a94f7b7fb735,
            0x40006e1055148d81,
            0xc01b01f7fd9467a6,
        ];
        for want in want_laplace_bits {
            assert_eq!(laplace(5.0, &mut rng).to_bits(), want);
        }

        let mut rng = KeyedRandom::new(&seed);
        let want_gaussian_bits: [u64; 6] = [
            0xc01e0cf24a1db8f5,
            0xc0084ac3c41239e1,
            0x4017dd977eb825ce,
            0xc02dfbfd63a1900b,
            0xc025898459851e77,
            0xc028823797a0c614,
        ];
        for want in want_gaussian_bits {
            assert_eq!(gaussian(9.682458365518542, &mut rng).to_bits(), want);
        }

        let mut rng = KeyedRandom::new(&seed);
        let got_discrete: Vec<i64> = (0..8).map(|_| discrete_laplace(5.0, &mut rng)).collect();
        assert_eq!(got_discrete, vec![0, 2, 11, -1, 2, -7, -3, 0]);
    }

    fn hex_to_bytes32(hex: &str) -> [u8; 32] {
        let mut out = [0u8; 32];
        for (i, slot) in out.iter_mut().enumerate() {
            *slot = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap();
        }
        out
    }

    #[test]
    fn golden_query_draws_match_oracle_bit_for_bit() {
        let secret = b"privguard-golden-secret-v1";

        // Verify all 20 golden query fingerprints and derived seeds match oracle/tests/golden/queries.json
        let golden_seeds: [(&str, &str, &str); 20] = [
            (
                "COUNT|*|visits|GROUP=",
                "edc6f9946aed8757f617dac4a6ea3c1120f72cbaac6267577007b2db661238a5",
                "fea9e31a56a25426bc55ac900511eae055d1f70424a000c9170d98f7c7559b4d",
            ),
            (
                "COUNT|*|visits|admission_typeexact'ELECTIVE'|GROUP=",
                "73d362984f9ee05460c3519bcc109e5ff842b1331051438fac2dcd9f4eb5b028",
                "435f44e59ae518f7cd6e14e8fb91e97c9e6bc72efd1b4bcddf2e6e56845fb7c8",
            ),
            (
                "COUNT|*|visits|dept_idexact2|total_chargegt500.0|GROUP=",
                "9d684a561ecd2000d5d42450fe4f55ae4432ab3a5a87bd7fe34757c9a91306d9",
                "233805eff32c72c996a3cb141cf9724ab46cb660240460681f9a0fcdb269c0ca",
            ),
            (
                "COUNT|*|visits|admission_typene'TRAUMA'|GROUP=",
                "5cac57282ef62480d5662d97ffb084f0193757631fd3bd768e5fdb283fcfcd90",
                "c5f110e9bfe1e93511fe73f3b058ec7485fc3fefb0c36ce63bcfcd32c6f14eed",
            ),
            (
                "COUNT|*|visits|admit_tsgte'2024-03-01 00:00:00'|admit_tslt'2024-09-01 00:00:00'|GROUP=",
                "49305a96d25e84026489b2802a827471b16faae622e8d52dbeec270cf038daa5",
                "d3adf55cdb236e448565a5b134d81cc2e96e73fd88a694c802f91e497a874772",
            ),
            (
                "SUM|total_charge|visits|GROUP=",
                "9f30660eaf3eea9fff615504dcea6f85827f57c2ac37bc2fc013f197463453a4",
                "1a4a1b93307c4f7d96372e5d0ec6228f7c59bf0c69e5699833ce8082b13a29cc",
            ),
            (
                "SUM|total_charge|visits|dept_idexact1|GROUP=",
                "1396a02581594ba9bad1208cafb8924450815e6c1a29aeb150de18c0ca7ca36a",
                "06037cdcbb55e12ecdf8d9076d03cde8064d86bc544dfd0fb76d120a813c4279",
            ),
            (
                "SUM|total_charge|visits|total_chargegte100.0|total_chargelte900.0|GROUP=",
                "0e893e3a00f9aa6224153263ec0290fc48c6e0236f2f2b0f70b12aff93f54096",
                "eccca5925170f01e525fe4e70ae5cd04791915fb5bdc9ea0da4803b5884810ce",
            ),
            (
                "SUM|total_charge|visits|GROUP=",
                "9f30660eaf3eea9fff615504dcea6f85827f57c2ac37bc2fc013f197463453a4",
                "1a4a1b93307c4f7d96372e5d0ec6228f7c59bf0c69e5699833ce8082b13a29cc",
            ),
            (
                "AVG|total_charge|visits|GROUP=",
                "3277682c2c75e2aafcc8e5b34cde9bd4aaf65180ed4f09fbc72cbc7011c46029",
                "701588ec87743dccbe0b34769d57351c96fee5cbf8a90ba404bd385b989b707a",
            ),
            (
                "AVG|total_charge|visits|admission_typeexact'EMERGENCY'|GROUP=",
                "d9fa1ca04cfe525412de3a9415d58779b153df1f82dda77b0618d683139eff12",
                "aaaaadf1a01c685e635a5cca1e743e0fb32354f3b3361dca46cb58af7b9ea7c8",
            ),
            (
                "AVG|total_charge|visits|total_chargelt800.0|GROUP=",
                "9d22462741c037883ba36dc2fbd2f276dfdd0a4168dc775223a44355afe819b2",
                "4a8d24c198a9f9028efad89cbf901e50b44fd3feedaf0cb68c4401bb84d6723d",
            ),
            (
                "COUNT|*|visits|GROUP=admission_type",
                "26a6c1030beb904860335b4316fd9e5a6a875ff16bb5a2c6c8e6cd88e1419c10",
                "6fff473f3aee8b4d13bd524960640b65f6a84c3b517c8776d114039d06ab323f",
            ),
            (
                "COUNT|*|visits|GROUP=dept_id",
                "df35e32a951eff76666671ce50cb9f3705b9c25e5c74694a5c89723b6a3017f9",
                "561f494b3ab005265f2f6e6b54fa118f22fd9cab4b28bfbea6d367909c318e9d",
            ),
            (
                "COUNT|*|visits|GROUP=admission_type",
                "26a6c1030beb904860335b4316fd9e5a6a875ff16bb5a2c6c8e6cd88e1419c10",
                "6fff473f3aee8b4d13bd524960640b65f6a84c3b517c8776d114039d06ab323f",
            ),
            (
                "COUNT|*|visits|total_chargegt300.0|GROUP=admission_type",
                "3a9eb15d9cfdebb075dbc619ba45b53406685efcd39227ce004b648840cad4dd",
                "3a459db7222e4024bc6e2f7dddde95e0341deb6f4a0286c50af9d39cfa4013e8",
            ),
            (
                "SUM|total_charge|visits|GROUP=admission_type",
                "643eff58586668481d00a3f700cf534ad8731fb59e7d8ccefd49578f0d512bbb",
                "fdef3a088dbf0471595d6ae03a2036f05bff06e9cedc2a76b324e834b87ccbb3",
            ),
            (
                "SUM|total_charge|visits|total_chargegte0.0|GROUP=dept_id",
                "aa55405f12964b68e447a43ff18681c01aaffa4ff7abf60a131deca7b607aef3",
                "c48b5210f9df41217cbef3ccf5a0444bcca91208cacfed1b216e3b15313a689f",
            ),
            (
                "SUM|total_charge|visits|GROUP=admission_type",
                "643eff58586668481d00a3f700cf534ad8731fb59e7d8ccefd49578f0d512bbb",
                "fdef3a088dbf0471595d6ae03a2036f05bff06e9cedc2a76b324e834b87ccbb3",
            ),
            (
                "COUNT|*|visits|GROUP=dept_id",
                "df35e32a951eff76666671ce50cb9f3705b9c25e5c74694a5c89723b6a3017f9",
                "561f494b3ab005265f2f6e6b54fa118f22fd9cab4b28bfbea6d367909c318e9d",
            ),
        ];
        for (canonical, want_fp, want_seed) in golden_seeds {
            let digest = sha256(canonical.as_bytes());
            assert_eq!(QueryFingerprint::from_digest(digest).to_hex(), want_fp);
            let seed = derive_seed(secret, &digest, 1, b"");
            assert_eq!(QueryFingerprint::from_digest(seed).to_hex(), want_seed);
        }

        // Q1-Q5: discrete_laplace ungrouped counts
        let dl_vectors: [(&str, f64, i64); 5] = [
            (
                "fea9e31a56a25426bc55ac900511eae055d1f70424a000c9170d98f7c7559b4d",
                5.0,
                0,
            ),
            (
                "435f44e59ae518f7cd6e14e8fb91e97c9e6bc72efd1b4bcddf2e6e56845fb7c8",
                5.0,
                -10,
            ),
            (
                "233805eff32c72c996a3cb141cf9724ab46cb660240460681f9a0fcdb269c0ca",
                laplace_scale_from_rho(5.0, 0.25),
                -17,
            ),
            (
                "c5f110e9bfe1e93511fe73f3b058ec7485fc3fefb0c36ce63bcfcd32c6f14eed",
                laplace_scale_from_rho(2.0, 1.0),
                1,
            ),
            (
                "d3adf55cdb236e448565a5b134d81cc2e96e73fd88a694c802f91e497a874772",
                5.0,
                1,
            ),
        ];
        for (seed_hex, scale, want) in dl_vectors {
            let seed = hex_to_bytes32(seed_hex);
            assert_eq!(discrete_laplace(scale, &mut KeyedRandom::new(&seed)), want);
        }

        // Q6-Q9: laplace ungrouped sums (bit-for-bit IEEE-754)
        let sum_vectors: [(&str, f64, u64); 4] = [
            (
                "1a4a1b93307c4f7d96372e5d0ec6228f7c59bf0c69e5699833ce8082b13a29cc",
                laplace_scale_from_rho(5000.0, 0.5),
                0x40b383641a3ea9cc,
            ),
            (
                "06037cdcbb55e12ecdf8d9076d03cde8064d86bc544dfd0fb76d120a813c4279",
                laplace_scale_from_rho(5000.0, 0.5),
                0xc0b0647a88713894,
            ),
            (
                "eccca5925170f01e525fe4e70ae5cd04791915fb5bdc9ea0da4803b5884810ce",
                laplace_scale_from_rho(5000.0, 0.5),
                0x4092f343feaf7ab3,
            ),
            (
                "1a4a1b93307c4f7d96372e5d0ec6228f7c59bf0c69e5699833ce8082b13a29cc",
                laplace_scale_from_rho(1600.0, 3.0),
                0x408464c2de703b31,
            ),
        ];
        for (seed_hex, scale, want_bits) in sum_vectors {
            let seed = hex_to_bytes32(seed_hex);
            assert_eq!(
                laplace(scale, &mut KeyedRandom::new(&seed)).to_bits(),
                want_bits
            );
        }

        // Q10-Q12: AVG dual laplace draws (bit-for-bit IEEE-754)
        let avg_vectors: [(&str, f64, f64, u64, u64); 3] = [
            (
                "701588ec87743dccbe0b34769d57351c96fee5cbf8a90ba404bd385b989b707a",
                laplace_scale_from_rho(5000.0, 0.25),
                laplace_scale_from_rho(5.0, 0.25),
                0x40a075ff2ea657c1,
                0xc0201f8a921d2f0a,
            ),
            (
                "aaaaadf1a01c685e635a5cca1e743e0fb32354f3b3361dca46cb58af7b9ea7c8",
                laplace_scale_from_rho(5000.0, 0.25),
                laplace_scale_from_rho(5.0, 0.25),
                0xc0ccdb230e47a07b,
                0xc02be4adfb16007c,
            ),
            (
                "4a8d24c198a9f9028efad89cbf901e50b44fd3feedaf0cb68c4401bb84d6723d",
                laplace_scale_from_rho(3200.0, 1.28),
                laplace_scale_from_rho(4.0, 1.28),
                0xc09e0a0ca819a6b4,
                0xbfdf737b5af9625a,
            ),
        ];
        for (seed_hex, s_sum, s_cnt, want_sum, want_cnt) in avg_vectors {
            let seed = hex_to_bytes32(seed_hex);
            let mut rng = KeyedRandom::new(&seed);
            assert_eq!(laplace(s_sum, &mut rng).to_bits(), want_sum);
            assert_eq!(laplace(s_cnt, &mut rng).to_bits(), want_cnt);
        }

        // Q13-Q20: Grouped partition selection (laplace) + aggregate (gaussian) draws (bit-for-bit IEEE-754)
        type GroupedVector<'a> = (&'a str, f64, u32, f64, [u64; 4], &'a [u64]);
        let grouped_vectors: [GroupedVector<'_>; 8] = [
            (
                "6fff473f3aee8b4d13bd524960640b65f6a84c3b517c8776d114039d06ab323f",
                0.5,
                3,
                5.0,
                [
                    0x3fdb327fb7e11a39,
                    0x3fbd6f29f143aea0,
                    0x400ed22b4bf22acd,
                    0x3ff98d95f69406ba,
                ],
                &[
                    0xc013066765431615,
                    0x401e3bc4f73679d0,
                    0xc02a1b9d98b73bd2,
                    0x402fee66d6e67250,
                ],
            ),
            (
                "561f494b3ab005265f2f6e6b54fa118f22fd9cab4b28bfbea6d367909c318e9d",
                0.5,
                3,
                5.0,
                [
                    0x400e324969988546,
                    0x3ffc25b5b1964e99,
                    0xbff0059e2aee70a7,
                    0x40038b6925ffe8ed,
                ],
                &[0xc009799d01d61482, 0xc02707da326c0b9f, 0xc02b83a5814392b2],
            ),
            (
                "6fff473f3aee8b4d13bd524960640b65f6a84c3b517c8776d114039d06ab323f",
                4.0,
                3,
                5.0,
                [
                    0x3fc33b3aed75ddc9,
                    0x3fa4d02a5abb4f95,
                    0x3ff5cb312d2074f4,
                    0x3fe21199844401e9,
                ],
                &[
                    0xbffae7ca8e4e0244,
                    0x400560d7eabcc246,
                    0xc0127607a8f3840e,
                    0x4016942cc5539185,
                ],
            ),
            (
                "3a459db7222e4024bc6e2f7dddde95e0341deb6f4a0286c50af9d39cfa4013e8",
                0.5,
                3,
                5.0,
                [
                    0xc021083cf8f02d72,
                    0x3fcf820021faeae6,
                    0x4000db5545f8513d,
                    0x3fee88dfe2d225f8,
                ],
                &[
                    0xc02f45e921414353,
                    0xc02ae51734921aac,
                    0xc0179f3d954f30a5,
                    0xc02a6f727feb22ee,
                ],
            ),
            (
                "fdef3a088dbf0471595d6ae03a2036f05bff06e9cedc2a76b324e834b87ccbb3",
                0.5,
                3,
                5000.0,
                [
                    0x40216ff956af8cc2,
                    0x3ff4334ad3d06b9f,
                    0xbfe7375dae7890d1,
                    0xbff1c5879e83a40a,
                ],
                &[
                    0x40d21b660729a3a9,
                    0xc0be011f99d2e009,
                    0x40980fd1aa0d1c45,
                    0x40b2af006c08f419,
                ],
            ),
            (
                "c48b5210f9df41217cbef3ccf5a0444bcca91208cacfed1b216e3b15313a689f",
                1.0,
                3,
                5000.0,
                [
                    0xbfeb6b9c297acd8c,
                    0x400963d4b8a39258,
                    0x3fe9aac9b9ebd0a5,
                    0x4011007624e37542,
                ],
                &[
                    0xc083fb801933dad3,
                    0x40a60239612ea138,
                    0xc0564621cd2ed1b7,
                    0x406cfae0980e4821,
                ],
            ),
            (
                "fdef3a088dbf0471595d6ae03a2036f05bff06e9cedc2a76b324e834b87ccbb3",
                0.5,
                1,
                800.0,
                [
                    0x40216ff956af8cc2,
                    0x3ff4334ad3d06b9f,
                    0xbfe7375dae7890d1,
                    0xbff1c5879e83a40a,
                ],
                &[0x409ac332481dca7e, 0xc0862c6a10c62809],
            ),
            (
                "561f494b3ab005265f2f6e6b54fa118f22fd9cab4b28bfbea6d367909c318e9d",
                0.02,
                3,
                5.0,
                [
                    0x4032df6de1ff534c,
                    0x402197918efdf120,
                    0xc0140705b5aa0cd1,
                    0x40286e436f7fe328,
                ],
                &[0xc02fd804424b99a1, 0xc04cc9d0bf070e86],
            ),
        ];
        for (seed_hex, rho, max_g, delta1, want_part, want_agg) in grouped_vectors {
            let seed = hex_to_bytes32(seed_hex);
            let mut part_sid = seed.to_vec();
            part_sid.extend_from_slice(b"partition");
            let mut agg_sid = seed.to_vec();
            agg_sid.extend_from_slice(b"aggregate");

            let mut part_rng = KeyedRandom::new(&part_sid);
            let mut agg_rng = KeyedRandom::new(&agg_sid);
            let part_scale = 1.0 / (2.0 * 0.2 * rho).sqrt();
            let sigma = sigma_from_rho(grouped_l2_sensitivity(max_g, delta1), 0.8 * rho);

            for want in want_part {
                assert_eq!(laplace(part_scale, &mut part_rng).to_bits(), want);
            }
            for &want in want_agg {
                assert_eq!(gaussian(sigma, &mut agg_rng).to_bits(), want);
            }
        }
    }

    #[test]
    fn clamp_bounds_both_ends_and_handles_none() {
        assert_eq!(clamp(-5.0, Some(0.0), Some(10.0)), 0.0);
        assert_eq!(clamp(50.0, Some(0.0), Some(10.0)), 10.0);
        assert_eq!(clamp(4.0, Some(0.0), Some(10.0)), 4.0);
        assert_eq!(clamp(-5.0, None, Some(10.0)), -5.0);
        assert_eq!(clamp(50.0, Some(0.0), None), 50.0);
    }
}
