# PrivGuard Attack Lab (`attacks/`)

Empirical adversarial evaluation of PrivGuard's differential-privacy defences.

## Scenarios

| Attack | Target property | Raw SQL result | PrivGuard result |
|---|---|---|---|
| **Differencing attack** | Reconstruct a single individual's value via $Q(D) - Q(D \setminus \{x\})$ | Exact leak | Masked by calibrated DP noise + overlap detection |
| **Repeat-and-average attack** | Average $N$ identical queries to cancel zero-mean noise | Exact (no noise) | Blocked by deterministic keyed noise + fingerprint memoisation ($\rho = 0$ cache hits return identical draw) |

## Usage

```bash
python attacks/demo.py
```

* **Phase 0**: runnable skeleton (`demo.py` stubs).
* **Phase 1**: live differencing and averaging attacks against raw PostgreSQL vs. the PrivGuard API (`:55001` / `:8000`).
* **Phase 4**: expanded v2 suite against the Rust engine (budget exhaustion, ledger tampering, SQL injection, JWT forgery).
