//! Keyed deterministic noise stream and exact DP mechanisms. `SPEC.md` §4.4 and A5
//! (`IMPLEMENTATION_PLAN.md` 3.2).
//!
//! Mirrors `oracle/privguard_oracle/mechanisms.py` bit-for-bit:
//! - `seed = SHA-256(server_secret || ast_hash || principal_id_be64 || salt)`
//! - the stream is `SHA-256(seed || counter_be64)` in counter mode, read 8 bytes at a time;
//! - integers below `n` come from 128-bit draws by rejection, so there is no modulo bias.
//!
//! **No floating-point sampling.** Noise from `ln`/`cos` is not differentially private: the
//! floats it can emit depend on the true value (Mironov, CCS 2012). Every answer is moved onto
//! an integer grid of step `unit / GRID`, noised with the exact discrete Laplace or discrete
//! Gaussian of Canonne, Kamath and Steinke (NeurIPS 2020, Algs. 1-3), which are integer
//! arithmetic only, and moved back. The released value is `cells * step`, a function of an
//! integer alone.

use privguard_core::{Error, Result};

/// Grid cells per `unit` (the per-privacy-unit L-infinity contribution bound).
pub const GRID: i128 = 1024;

/// Parameter ceilings keep every intermediate inside `u128`; they are reached only at a
/// per-stage rho below ~5e-10, which is refused.
const MAX_LAPLACE_SCALE: f64 = (1u64 << 40) as f64;
const MAX_GAUSSIAN_VARIANCE: f64 = (1u64 << 50) as f64;

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

/// Deterministic byte stream over `SHA-256(seed || counter_be64)`, read 8 bytes at a time.
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

    pub fn next_u64(&mut self) -> u64 {
        if self.pos + 8 > 32 {
            self.refill();
        }
        let chunk = u64::from_be_bytes(self.buf[self.pos..self.pos + 8].try_into().unwrap());
        self.pos += 8;
        chunk
    }

    /// Two consecutive 64-bit words, the first one the high half.
    pub fn next_u128(&mut self) -> u128 {
        let hi = u128::from(self.next_u64());
        (hi << 64) | u128::from(self.next_u64())
    }

    /// Uniform integer in `[0, n)` by rejection (no modulo bias). `n == 1` draws nothing.
    pub fn below(&mut self, n: u128) -> u128 {
        assert!(n >= 1, "below(0)");
        if n == 1 {
            return 0;
        }
        // 2^128 mod n, computed without overflowing.
        let reject = (u128::MAX % n + 1) % n;
        loop {
            let x = self.next_u128();
            if x <= u128::MAX - reject {
                return x % n;
            }
        }
    }
}

// --- exact samplers (Canonne, Kamath & Steinke 2020) -------------------------------------

/// Bernoulli(exp(-p/q)) for 0 <= p <= q (Alg. 1).
fn bern_unit(p: u128, q: u128, rng: &mut KeyedRandom) -> bool {
    let mut k: u128 = 1;
    while rng.below(q.saturating_mul(k)) < p {
        k += 1;
    }
    k % 2 == 1
}

/// Bernoulli(exp(-p/q)) for any p >= 0.
fn bern_exp(mut p: u128, q: u128, rng: &mut KeyedRandom) -> bool {
    while p > q {
        if !bern_unit(1, 1, rng) {
            return false;
        }
        p -= q;
    }
    bern_unit(p, q, rng)
}

/// `Z` with `P(Z = z)` proportional to `exp(-|z| / t)`, `t` a positive integer (Alg. 2, s = 1).
pub fn dlap(t: u128, rng: &mut KeyedRandom) -> i128 {
    assert!(t >= 1, "dlap scale must be positive");
    loop {
        let u = rng.below(t);
        if !bern_exp(u, t, rng) {
            continue;
        }
        let mut v: u128 = 0;
        while bern_unit(1, 1, rng) {
            v += 1;
        }
        let x = (u + t * v) as i128;
        let negative = rng.below(2) == 1;
        if negative && x == 0 {
            continue;
        }
        return if negative { -x } else { x };
    }
}

/// `floor(sqrt(n))`.
fn isqrt(n: u128) -> u128 {
    let mut r = (n as f64).sqrt() as u128;
    while r * r > n {
        r -= 1;
    }
    while (r + 1) * (r + 1) <= n {
        r += 1;
    }
    r
}

/// `Z` with `P(Z = z)` proportional to `exp(-z^2 / (2 variance))`, `variance` a positive
/// integer (Alg. 3).
pub fn dgauss(variance: u128, rng: &mut KeyedRandom) -> i128 {
    assert!(variance >= 1, "dgauss variance must be positive");
    let t = isqrt(variance) + 1; // floor(sigma) + 1
    loop {
        let y = dlap(t, rng);
        // gamma = (|y| t - variance)^2 / (2 variance t^2)
        let yt = y.unsigned_abs().saturating_mul(t);
        let d = yt.abs_diff(variance);
        let num = d.saturating_mul(d);
        let den = 2 * variance * t * t;
        if bern_exp(num, den, rng) {
            return y;
        }
    }
}

// --- parameters: real rho -> conservative integers ---------------------------------------

fn bad_rho(rho: f64) -> Error {
    Error::policy(format!(
        "rho must be positive, finite and not below ~5e-10 for exact sampling, got {rho}"
    ))
}

/// Integer scale `T` with `cells / T <= sqrt(2 rho)`: pure eps no larger than rho-zCDP allows.
fn laplace_scale(rho: f64, cells: i128) -> Result<u128> {
    if !(rho > 0.0 && rho.is_finite()) {
        return Err(bad_rho(rho));
    }
    let t = (cells as f64 / (2.0 * rho).sqrt()).ceil();
    if t > MAX_LAPLACE_SCALE {
        return Err(bad_rho(rho));
    }
    Ok((t as u128).max(1))
}

/// Integer variance `V` with `cells_sq / (2 V) <= rho`.
fn gaussian_variance(rho: f64, cells_sq: i128) -> Result<u128> {
    if !(rho > 0.0 && rho.is_finite()) {
        return Err(bad_rho(rho));
    }
    let v = (cells_sq as f64 / (2.0 * rho)).ceil();
    if v > MAX_GAUSSIAN_VARIANCE {
        return Err(bad_rho(rho));
    }
    Ok((v as u128).max(1))
}

fn step(unit: f64) -> Result<f64> {
    if unit > 0.0 && unit.is_finite() {
        Ok(unit / GRID as f64)
    } else {
        Err(Error::policy("unit must be positive and finite"))
    }
}

/// Nearest grid cell, ties up (the oracle does the same; Python's `round` would not).
fn cells_of(value: f64, step: f64) -> i128 {
    (value / step + 0.5).floor() as i128
}

/// rho-zCDP release of `value` whose L1 sensitivity is `unit`.
///
/// Sensitivity in cells is `GRID + 1`: `GRID` for the data, one more for rounding `value` onto
/// the grid.
pub fn noisy_laplace(value: f64, unit: f64, rho: f64, rng: &mut KeyedRandom) -> Result<f64> {
    let step = step(unit)?;
    let cells = cells_of(value, step) + dlap(laplace_scale(rho, GRID + 1)?, rng);
    Ok(cells as f64 * step)
}

/// [`noisy_laplace`] for an integer count, rounded back to an integer (post-processing).
pub fn noisy_count(n: i64, unit: f64, rho: f64, rng: &mut KeyedRandom) -> Result<i64> {
    Ok((noisy_laplace(n as f64, unit, rho, rng)? + 0.5).floor() as i64)
}

/// rho-zCDP release of one coordinate of a vector with at most `groups` coordinates each
/// moving by at most `unit` (L2 sensitivity `sqrt(groups) * unit`).
pub fn noisy_gaussian(
    value: f64,
    unit: f64,
    groups: u32,
    rho: f64,
    rng: &mut KeyedRandom,
) -> Result<f64> {
    let step = step(unit)?;
    let d = GRID + 1;
    let variance = gaussian_variance(rho, i128::from(groups) * d * d)?;
    let cells = cells_of(value, step) + dgauss(variance, rng);
    Ok(cells as f64 * step)
}

// --- partition selection ---------------------------------------------------------------

/// Noise scale and release cutoff for DP partition selection. `SPEC.md` §4.5.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PartitionCutoff {
    /// Integer scale `T` of the discrete Laplace noise, in cells of `1 / GRID`.
    pub scale: u128,
    /// A single-unit group is released iff its noise is `>= cutoff`, with probability
    /// `e^(-K/T) / (1 + e^(-1/T)) <= delta_part` (the discrete Laplace tail).
    pub cutoff: i128,
}

/// Counts are integers, so they sit exactly on the grid and need no rounding slack:
/// sensitivity is `GRID` cells.
pub fn partition_cutoff(rho_part: f64, delta_part: f64) -> Result<PartitionCutoff> {
    let t = laplace_scale(rho_part, GRID)?;
    let tf = t as f64;
    let k = (tf * (1.0 / (delta_part * (1.0 + (-1.0 / tf).exp()))).ln()).ceil();
    Ok(PartitionCutoff {
        scale: t,
        cutoff: (k as i128).max(1),
    })
}

/// Whether a group of `units` distinct privacy units is released.
pub fn partition_release(units: u64, cut: &PartitionCutoff, rng: &mut KeyedRandom) -> bool {
    let cells = i128::from(units) * GRID + dlap(cut.scale, rng);
    cells >= GRID + cut.cutoff
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

    const GOLDEN_SEED: &str = "fea9e31a56a25426bc55ac900511eae055d1f70424a000c9170d98f7c7559b4d";

    #[test]
    fn the_seed_matches_the_oracle() {
        let secret = b"privguard-golden-secret-v1";
        let fp = canonical_query("COUNT", None, None).fingerprint().unwrap();
        assert_eq!(
            fp.to_hex(),
            "edc6f9946aed8757f617dac4a6ea3c1120f72cbaac6267577007b2db661238a5"
        );
        let seed = derive_seed(secret, fp.as_bytes(), 1, b"");
        assert_eq!(QueryFingerprint::from_digest(seed).to_hex(), GOLDEN_SEED);
    }

    /// Golden values from `oracle/privguard_oracle/mechanisms.py`, same seed. Integer samplers
    /// must agree exactly; the released floats must agree bit for bit.
    #[test]
    fn stream_and_samplers_match_oracle_exactly() {
        let seed = hex_to_bytes32(GOLDEN_SEED);
        let rng = || KeyedRandom::new(&seed);

        let mut r = rng();
        let words: Vec<u64> = (0..4).map(|_| r.next_u64()).collect();
        assert_eq!(
            words,
            [
                0x83a1cb9f0bd8461f,
                0xa1efcf9e9e0a1737,
                0xf27bb199799b42b3,
                0x7357681da95355dc
            ]
        );
        assert_eq!(rng().next_u128(), 0x83a1cb9f0bd8461fa1efcf9e9e0a1737);

        let mut r = rng();
        let below: Vec<u128> = (0..6).map(|_| r.below(1000)).collect();
        assert_eq!(below, [343, 668, 629, 298, 757, 859]);
        assert_eq!(r.below(1), 0);
        assert_eq!(r.below(1u128 << 100), 608654602713823083849953925208);

        let mut r = rng();
        let got: Vec<i128> = (0..10).map(|_| dlap(5, &mut r)).collect();
        assert_eq!(got, [3, -7, 11, 2, -1, 6, 6, -8, -1, 1]);
        let mut r = rng();
        let got: Vec<i128> = (0..6).map(|_| dlap(1000, &mut r)).collect();
        assert_eq!(got, [343, -1757, 2456, 188, 528, -337]);

        let mut r = rng();
        let got: Vec<i128> = (0..10).map(|_| dgauss(50, &mut r)).collect();
        assert_eq!(got, [-7, 3, 8, 10, 1, 7, 8, -14, -7, -9]);
        let mut r = rng();
        let got: Vec<i128> = (0..5).map(|_| dgauss(100_000_000, &mut r)).collect();
        assert_eq!(got, [-4387, 7275, -789, 9605, -7058]);

        let mut r = rng();
        let got: Vec<i64> = (0..6)
            .map(|_| noisy_count(211, 5.0, 0.5, &mut r).unwrap())
            .collect();
        assert_eq!(got, [208, 215, 211, 216, 215, 195]);

        let mut r = rng();
        let got: Vec<u64> = (0..4)
            .map(|_| {
                noisy_laplace(123456.789, 5000.0, 0.5, &mut r)
                    .unwrap()
                    .to_bits()
            })
            .collect();
        assert_eq!(
            got,
            [
                0x40fd6ca760000000,
                0x40ff1d1680000000,
                0x40fe1c2140000000,
                0x40ff6c6f00000000
            ]
        );

        let mut r = rng();
        let got: Vec<u64> = (0..4)
            .map(|_| {
                noisy_gaussian(1000.5, 5000.0, 3, 0.4, &mut r)
                    .unwrap()
                    .to_bits()
            })
            .collect();
        assert_eq!(
            got,
            [
                0x409aee8800000000,
                0x40c8ce1900000000,
                0x40b4d8ea00000000,
                0x40b0e14a00000000
            ]
        );

        let cut = |rho, delta| {
            let c = partition_cutoff(rho, delta).unwrap();
            (c.scale, c.cutoff)
        };
        assert_eq!(cut(0.01, 1e-2), (7241, 28328));
        assert_eq!(cut(0.1, 1e-2), (2290, 8960));
        assert_eq!(cut(0.5, 1e-3), (1024, 6365));
        let c = partition_cutoff(0.1, 1e-2).unwrap();
        let mut r = rng();
        let cells: Vec<i128> = (0..4).map(|_| 3 * GRID + dlap(c.scale, &mut r)).collect();
        assert_eq!(cells, [3715, -575, 8138, 4474]);
    }

    #[test]
    fn samplers_follow_their_exact_distributions() {
        let n = 40_000usize;
        let mut r = KeyedRandom::new(b"dlap-pmf");
        let t = 3f64;
        let c = (1.0 - (-1.0 / t).exp()) / (1.0 + (-1.0 / t).exp());
        let draws: Vec<i128> = (0..n).map(|_| dlap(3, &mut r)).collect();
        for z in -4i128..=4 {
            let want = c * (-(z.abs() as f64) / t).exp();
            let got = draws.iter().filter(|&&d| d == z).count() as f64 / n as f64;
            assert!(
                (got - want).abs() < 5.0 * (want * (1.0 - want) / n as f64).sqrt(),
                "dlap z={z}: {got} vs {want}"
            );
        }

        let var = 9f64;
        let mut r = KeyedRandom::new(b"dgauss-pmf");
        let draws: Vec<i128> = (0..n).map(|_| dgauss(9, &mut r)).collect();
        let norm: f64 = (-60i32..=60)
            .map(|z| (-(z * z) as f64 / (2.0 * var)).exp())
            .sum();
        for z in -6i128..=6 {
            let want = (-((z * z) as f64) / (2.0 * var)).exp() / norm;
            let got = draws.iter().filter(|&&d| d == z).count() as f64 / n as f64;
            assert!(
                (got - want).abs() < 5.0 * (want * (1.0 - want) / n as f64).sqrt(),
                "dgauss z={z}: {got} vs {want}"
            );
        }
    }

    /// Mironov (2012): the released float must not depend on the input's low-order bits.
    #[test]
    fn released_values_lie_on_the_grid_whatever_the_input() {
        let (unit, rho) = (5000.0, 0.3);
        let step = unit / GRID as f64;
        for i in 0..200u32 {
            for value in [0.1, 0.1 + 2f64.powi(-40), 1234.5678, 1234.5678 + 1e-9] {
                let seed = i.to_be_bytes();
                let out = noisy_laplace(value, unit, rho, &mut KeyedRandom::new(&seed)).unwrap();
                assert_eq!((out / step).round() * step, out);
                let out =
                    noisy_gaussian(value, unit, 3, rho, &mut KeyedRandom::new(&seed)).unwrap();
                assert_eq!((out / step).round() * step, out);
            }
        }
    }

    #[test]
    fn parameters_round_toward_more_noise() {
        for rho in [0.001, 0.05, 0.3, 2.0, 4.0] {
            let t = laplace_scale(rho, GRID + 1).unwrap();
            assert!((GRID + 1) as f64 / t as f64 <= (2.0 * rho).sqrt() + 1e-12);
            let cells_sq = 3 * (GRID + 1) * (GRID + 1);
            let v = gaussian_variance(rho, cells_sq).unwrap();
            assert!(cells_sq as f64 / (2.0 * v as f64) <= rho + 1e-12);
        }
    }

    #[test]
    fn invalid_or_tiny_rho_is_refused() {
        let mut r = KeyedRandom::new(b"r");
        for rho in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(noisy_laplace(1.0, 10.0, rho, &mut r).is_err());
            assert!(noisy_gaussian(1.0, 10.0, 1, rho, &mut r).is_err());
        }
        assert!(noisy_gaussian(1.0, 10.0, 1, 1e-12, &mut r).is_err());
        assert!(noisy_laplace(1.0, 0.0, 0.5, &mut r).is_err());
    }

    #[test]
    fn rounding_ties_go_up_like_the_oracle() {
        assert_eq!(cells_of(0.5, 1.0), 1);
        assert_eq!(cells_of(-0.5, 1.0), 0);
        assert_eq!(cells_of(1.5, 1.0), 2);
    }

    #[test]
    fn partition_cutoff_keeps_single_unit_release_under_delta() {
        for (rho, delta) in [(0.01, 1e-2), (0.1, 1e-2), (0.5, 1e-3)] {
            let c = partition_cutoff(rho, delta).unwrap();
            let t = c.scale as f64;
            let tail = |k: i128| (-(k as f64) / t).exp() / (1.0 + (-1.0 / t).exp());
            assert!(tail(c.cutoff) <= delta * (1.0 + 1e-9));
            assert!(tail(c.cutoff - 1) > delta, "cutoff is not the smallest");
        }
    }

    fn hex_to_bytes32(hex: &str) -> [u8; 32] {
        let mut out = [0u8; 32];
        for (i, slot) in out.iter_mut().enumerate() {
            *slot = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap();
        }
        out
    }

    #[test]
    fn golden_query_fingerprints_and_seeds_match_oracle() {
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
