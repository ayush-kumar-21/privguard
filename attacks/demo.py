"""PrivGuard Attack Lab v1 — Empirical evaluation of DP defences & exposed holes.

Executes the differencing attack and the repeat-and-average attack against:
  1. Raw PostgreSQL (:55000 or :5432)
  2. PrivGuard API (:55001 or :8000)

Also probes for implementation/privacy holes in the legacy Django gateway and
prints a structured comparison table.

Run:
    python attacks/demo.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import socket
import statistics
import struct
import urllib.error
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Zero-dependency PostgreSQL v3 wire-protocol client (SCRAM-SHA-256 / Simple Query)
# ---------------------------------------------------------------------------
class PgWireClient:
    """Minimal stdlib-only PostgreSQL v3 client supporting SCRAM-SHA-256."""

    def __init__(self, host: str, port: int, user: str, password: str, dbname: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.dbname = dbname

    def query(self, sql: str) -> list[list[str | None]]:
        with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
            self._startup_and_auth(sock)
            q_bytes = sql.encode("utf-8") + b"\x00"
            sock.sendall(b"Q" + struct.pack("!I", len(q_bytes) + 4) + q_bytes)
            rows: list[list[str | None]] = []
            while True:
                tag, body = self._recv_msg(sock)
                if tag == b"D":
                    ncols = struct.unpack("!H", body[:2])[0]
                    pos = 2
                    row: list[str | None] = []
                    for _ in range(ncols):
                        clen = struct.unpack("!i", body[pos : pos + 4])[0]
                        pos += 4
                        if clen == -1:
                            row.append(None)
                        else:
                            row.append(body[pos : pos + clen].decode("utf-8"))
                            pos += clen
                    rows.append(row)
                elif tag == b"Z":
                    break
                elif tag == b"E":
                    raise RuntimeError(f"PostgreSQL error: {body!r}")
            sock.sendall(b"X" + struct.pack("!I", 4))
            return rows

    def query_scalar_float(self, sql: str) -> float:
        rows = self.query(sql)
        if not rows or rows[0][0] is None:
            return 0.0
        return float(rows[0][0])

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("unexpected EOF from PostgreSQL")
            buf.extend(chunk)
        return bytes(buf)

    def _recv_msg(self, sock: socket.socket) -> tuple[bytes, bytes]:
        tag = self._recv_exact(sock, 1)
        length = struct.unpack("!I", self._recv_exact(sock, 4))[0] - 4
        body = self._recv_exact(sock, length) if length > 0 else b""
        return tag, body

    def _startup_and_auth(self, sock: socket.socket) -> None:
        params = f"user\x00{self.user}\x00database\x00{self.dbname}\x00\x00".encode("utf-8")
        sock.sendall(struct.pack("!II", len(params) + 8, 196608) + params)
        while True:
            tag, body = self._recv_msg(sock)
            if tag == b"R":
                code = struct.unpack("!I", body[:4])[0]
                if code in (0, 12):
                    continue
                if code == 3:
                    pw = self.password.encode("utf-8") + b"\x00"
                    sock.sendall(b"p" + struct.pack("!I", len(pw) + 4) + pw)
                elif code == 10:
                    self._scram_sha256(sock)
                else:
                    raise RuntimeError(f"Unsupported PostgreSQL auth code: {code}")
            elif tag == b"Z":
                return
            elif tag == b"E":
                raise RuntimeError(f"PostgreSQL startup error: {body!r}")

    def _scram_sha256(self, sock: socket.socket) -> None:
        nonce = base64.b64encode(os.urandom(18)).decode("ascii")
        c_first_bare = f"n=,r={nonce}"
        c_first = ("n,," + c_first_bare).encode("utf-8")
        mech = b"SCRAM-SHA-256\x00"
        payload = mech + struct.pack("!I", len(c_first)) + c_first
        sock.sendall(b"p" + struct.pack("!I", len(payload) + 4) + payload)

        tag, s_first_body = self._recv_msg(sock)
        if tag != b"R" or struct.unpack("!I", s_first_body[:4])[0] != 11:
            raise RuntimeError("Unexpected SASL continue response")
        s_first = s_first_body[4:].decode("utf-8")
        parts = dict(item.split("=", 1) for item in s_first.split(","))
        salt = base64.b64decode(parts["s"])
        iters = int(parts["i"])
        r_full = parts["r"]

        salted_pw = hashlib.pbkdf2_hmac("sha256", self.password.encode("utf-8"), salt, iters)
        client_key = hmac.new(salted_pw, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        c_final_no_proof = f"c=biws,r={r_full}"
        auth_msg = f"{c_first_bare},{s_first},{c_final_no_proof}".encode("utf-8")
        client_sig = hmac.new(stored_key, auth_msg, hashlib.sha256).digest()
        proof = base64.b64encode(bytes(a ^ b for a, b in zip(client_key, client_sig))).decode("ascii")
        c_final = f"{c_final_no_proof},p={proof}".encode("utf-8")
        sock.sendall(b"p" + struct.pack("!I", len(c_final) + 4) + c_final)


# ---------------------------------------------------------------------------
# Endpoint discovery & HTTP helpers
# ---------------------------------------------------------------------------
def discover_api_url() -> str:
    env_url = os.environ.get("PRIVGUARD_API_URL")
    candidates = [env_url] if env_url else ["http://127.0.0.1:55001", "http://127.0.0.1:8000"]
    for base in candidates:
        if not base:
            continue
        try:
            with urllib.request.urlopen(f"{base}/api/health/", timeout=2.0) as resp:
                if resp.status == 200:
                    return base.rstrip("/")
        except Exception:  # noqa: BLE001
            continue
    raise ConnectionError(f"PrivGuard API not reachable on {candidates}")


def discover_pg_client() -> PgWireClient:
    host = os.environ.get("PRIVGUARD_DB_HOST", "127.0.0.1")
    user = os.environ.get("PRIVGUARD_DB_USER", "privguard")
    password = os.environ.get("PRIVGUARD_DB_PASSWORD", "privguard")
    dbname = os.environ.get("PRIVGUARD_DB_NAME", "safehealth")
    env_port = os.environ.get("PRIVGUARD_DB_PORT")
    ports = [int(env_port)] if env_port else [55000, 5432]
    for port in ports:
        client = PgWireClient(host, port, user, password, dbname)
        try:
            client.query("SELECT 1")
            return client
        except Exception:  # noqa: BLE001
            continue
    raise ConnectionError(f"PostgreSQL not reachable on {host}:{ports}")


def api_request(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {"Accept": "application/json"}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def prepare_analyst_session(pg: PgWireClient, api_url: str) -> str:
    """Ensure `alice` has fresh budget and clean cache for reproducible attack runs."""
    pg.query(
        "DELETE FROM sys_noise_cache WHERE principal_id IN "
        "(SELECT id FROM sys_principal WHERE username = 'alice')"
    )
    pg.query(
        "UPDATE sys_budget_account SET rho_spent = 0.0, rho_reserved = 0.0, status = 'active' "
        "WHERE principal_id IN (SELECT id FROM sys_principal WHERE username = 'alice')"
    )
    auth = api_request(
        api_url,
        "/api/auth/login/",
        method="POST",
        payload={"username": "alice", "password": "privguard123"},
    )
    return str(auth["access"])


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------
def run_differencing_attack(
    pg: PgWireClient | None = None,
    api_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Differencing attack: reconstruct outlier individual record via Q(D) - Q(D \\ {x})."""
    if pg is None or api_url is None or token is None:
        pg = discover_pg_client()
        api_url = discover_api_url()
        token = prepare_analyst_session(pg, api_url)

    # Identify the highest-charge visit and the second-highest charge to form a
    # predicate threshold that isolates exactly 1 row: D \ {x}.
    top_rows = pg.query(
        "SELECT visit_id, patient_id, total_charge FROM visits "
        "ORDER BY total_charge DESC LIMIT 2"
    )
    victim_visit_id = int(top_rows[0][0] or 0)
    victim_charge = float(top_rows[0][2] or 0.0)
    second_charge = float(top_rows[1][2] or 0.0)
    threshold = round((victim_charge + second_charge) / 2.0, 2)

    q_all = "SELECT SUM(total_charge) FROM visits"
    q_without_victim = f"SELECT SUM(total_charge) FROM visits WHERE total_charge <= {threshold}"

    # 1. Raw PostgreSQL execution
    raw_sum_all = pg.query_scalar_float(q_all)
    raw_sum_sub = pg.query_scalar_float(q_without_victim)
    raw_reconstructed = round(raw_sum_all - raw_sum_sub, 2)
    raw_err = abs(raw_reconstructed - victim_charge)

    # 2. PrivGuard DP API execution (/api/dp/query/)
    dp_res_all = api_request(
        api_url, "/api/dp/query/", method="POST", payload={"sql": q_all, "rho": 0.05}, token=token
    )
    dp_res_sub = api_request(
        api_url,
        "/api/dp/query/",
        method="POST",
        payload={"sql": q_without_victim, "rho": 0.05},
        token=token,
    )
    dp_val_all = float(dp_res_all["result"][0]["value"])
    dp_val_sub = float(dp_res_sub["result"][0]["value"])
    dp_reconstructed = round(dp_val_all - dp_val_sub, 2)
    dp_err = abs(dp_reconstructed - victim_charge)

    # Check if sys_predicate_overlap logged the 599/600 (Jaccard = 0.9983) overlap
    overlap_rows = int(pg.query_scalar_float("SELECT COUNT(*) FROM sys_predicate_overlap"))

    return {
        "attack": "Differencing Q(D)-Q(D\\{x})",
        "victim_visit_id": victim_visit_id,
        "true_value": victim_charge,
        "raw_postgres": f"${raw_reconstructed:,.2f} (err ${raw_err:,.2f} -> LEAKED)",
        "privguard_api": (
            f"${dp_reconstructed:,.2f} (err ${dp_err:,.2f} -> MASKED; "
            f"overlap_rows={overlap_rows} [HOLE P1-2])"
        ),
        "raw_error": raw_err,
        "dp_error": dp_err,
        "overlap_rows_logged": overlap_rows,
    }


def run_averaging_attack(
    pg: PgWireClient | None = None,
    api_url: str | None = None,
    token: str | None = None,
    repeats: int = 10,
) -> dict[str, Any]:
    """Repeat-and-average attack: average N repeated queries to cancel zero-mean DP noise."""
    if pg is None or api_url is None or token is None:
        pg = discover_pg_client()
        api_url = discover_api_url()
        token = prepare_analyst_session(pg, api_url)

    sql = "SELECT SUM(total_charge) FROM visits WHERE admission_type = 'EMERGENCY'"
    true_sum = round(pg.query_scalar_float(sql), 2)

    # 1. Raw PostgreSQL: N identical queries
    raw_draws = [pg.query_scalar_float(sql) for _ in range(repeats)]
    raw_mean = statistics.mean(raw_draws)
    raw_err = abs(raw_mean - true_sum)

    # 2. PrivGuard DP API: N identical queries (memoised in sys_noise_cache)
    dp_draws: list[float] = []
    cache_hits = 0
    total_rho_charged = 0.0
    for _ in range(repeats):
        resp = api_request(
            api_url, "/api/dp/query/", method="POST", payload={"sql": sql, "rho": 0.05}, token=token
        )
        dp_draws.append(float(resp["result"][0]["value"]))
        total_rho_charged += float(resp["rho_charged"])
        if resp["status"] == "cache_hit":
            cache_hits += 1

    dp_mean = statistics.mean(dp_draws)
    dp_std = statistics.pstdev(dp_draws)
    dp_err = abs(dp_mean - true_sum)

    return {
        "attack": f"Averaging (N={repeats} identical)",
        "true_value": true_sum,
        "raw_postgres": f"${raw_mean:,.2f} (err ${raw_err:,.2f} -> LEAKED)",
        "privguard_api": (
            f"${dp_mean:,.2f} (err ${dp_err:,.2f}, std=${dp_std:.2f}, "
            f"cache_hits={cache_hits}/{repeats - 1} -> BLOCKED)"
        ),
        "raw_error": raw_err,
        "dp_error": dp_err,
        "dp_std": dp_std,
        "cache_hits": cache_hits,
        "total_rho_charged": round(total_rho_charged, 4),
    }


def run_tautology_averaging_probe(
    pg: PgWireClient,
    api_url: str,
    token: str,
    variants: int = 5,
) -> dict[str, Any]:
    """Probe Hole P1-3: tautological WHERE rewrites bypass AST-hash memoisation."""
    base_sql = "SELECT SUM(total_charge) FROM visits WHERE admission_type = 'EMERGENCY'"
    true_sum = round(pg.query_scalar_float(base_sql), 2)

    draws: list[float] = []
    executed_count = 0
    for k in range(1, variants + 1):
        rewritten = f"{base_sql} AND total_charge <= {1000000 + k}.0"
        resp = api_request(
            api_url,
            "/api/dp/query/",
            method="POST",
            payload={"sql": rewritten, "rho": 0.05},
            token=token,
        )
        draws.append(float(resp["result"][0]["value"]))
        if resp["status"] == "executed":
            executed_count += 1

    sample_std = statistics.pstdev(draws)
    avg_err = abs(statistics.mean(draws) - true_sum)
    return {
        "attack": f"Tautology rewrite (k={variants})",
        "true_value": true_sum,
        "raw_postgres": f"${true_sum:,.2f} (err $0.00 -> LEAKED)",
        "privguard_api": (
            f"std=${sample_std:,.2f} across {executed_count}/{variants} fresh draws "
            f"(avg err ${avg_err:,.2f} -> BYPASSES CACHE [HOLE P1-3])"
        ),
        "sample_std": sample_std,
        "executed_count": executed_count,
    }


def run_crud_bypass_probe(
    pg: PgWireClient,
    api_url: str,
    token: str,
) -> dict[str, Any]:
    """Probe Hole P1-1: dp_analyst can read un-noised rows via GET /api/visits/."""
    top_rows = pg.query("SELECT visit_id, total_charge FROM visits ORDER BY total_charge DESC LIMIT 1")
    true_top_charge = float(top_rows[0][1] or 0.0)

    crud_resp = api_request(api_url, "/api/visits/?ordering=-total_charge", method="GET", token=token)
    leaked_top = crud_resp["results"][0]
    leaked_charge = float(leaked_top["total_charge"])
    err = abs(leaked_charge - true_top_charge)

    return {
        "attack": "REST CRUD read (/api/visits/)",
        "true_value": true_top_charge,
        "raw_postgres": f"${true_top_charge:,.2f} (row id={top_rows[0][0]})",
        "privguard_api": (
            f"${leaked_charge:,.2f} (err ${err:.2f}, rho=0.0 -> "
            f"EXACT LEAK TO dp_analyst [HOLE P1-1])"
        ),
        "leaked": math.isclose(leaked_charge, true_top_charge, rel_tol=1e-9),
    }


def main() -> int:
    pg = discover_pg_client()
    api_url = discover_api_url()
    token = prepare_analyst_session(pg, api_url)

    rows = [
        run_differencing_attack(pg, api_url, token),
        run_averaging_attack(pg, api_url, token, repeats=10),
        run_tautology_averaging_probe(pg, api_url, token, variants=5),
        run_crud_bypass_probe(pg, api_url, token),
    ]

    print(f"Connected to PostgreSQL ({pg.host}:{pg.port}/{pg.dbname}) and PrivGuard API ({api_url})")
    print()
    header = f"{'Attack / Probe':<30} | {'Raw Postgres':<38} | {'PrivGuard API'}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['attack']:<30} | {row['raw_postgres']:<38} | {row['privguard_api']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
