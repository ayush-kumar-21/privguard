"""Attack lab v2: the classic attacks, run against the real engine through the gateway.

Each test is one attack with its expected defence, so `attacks/README.md` stays honest: if a
defence regresses, CI fails. Every test gets a fresh engine (the overlap detector and noise cache
are per-database state, so tests must not see each other's queries).

Skipped when `privguardd` is not built; CI builds it first.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from privguard_gateway.app import create_app
from privguard_gateway.settings import Settings

from .conftest import PASSWORD, SECRET, auth, login
from .test_pg_engine import (
    ENGINE_BIN,
    ENGINE_SECRET,
    NOISE_SECRET,
    _free_port,
    _write_csvs,
    socket,
)

pytestmark = pytest.mark.skipif(ENGINE_BIN is None, reason="privguardd is not built")

COUNT = "SELECT COUNT(*) FROM visits"
SUM = "SELECT SUM(total_charge) FROM visits"


@pytest.fixture
def client(tmp_path, users) -> TestClient:
    csv, db = tmp_path / "csv", tmp_path / "db"
    _write_csvs(csv)
    env = {**os.environ, "PRIVGUARD_SERVER_SECRET": NOISE_SECRET, "PRIVGUARD_ENGINE_SECRET": ENGINE_SECRET}
    for step in (["load", str(csv), str(db)], ["seed-demo", str(db)]):
        subprocess.run([str(ENGINE_BIN), *step], check=True, env=env, capture_output=True)
    port = _free_port()
    proc = subprocess.Popen(
        [str(ENGINE_BIN), "serve", str(db), f"127.0.0.1:{port}"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("privguardd did not start listening")
        settings = Settings(jwt_secret=SECRET, engine_addr=f"127.0.0.1:{port}", engine_secret=ENGINE_SECRET)
        yield TestClient(create_app(settings, users=users))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def run(client, user, sql=COUNT, rho=0.05):
    return client.post("/api/dp/query/", headers=auth(client, user), json={"sql": sql, "rho": rho})


def budget(client, user):
    return client.get("/api/dp/budget/", headers=auth(client, user)).json()


# ------------------------------------------------------------------ privacy attacks
def test_differencing_by_excluding_a_few_rows_is_refused(client):
    """Q(D) - Q(D minus a few rows) would isolate those rows; near-duplicates are refused."""
    assert run(client, "alice", "SELECT COUNT(*) FROM visits WHERE total_charge >= 0", 0.5).status_code == 200
    second = run(client, "alice", "SELECT COUNT(*) FROM visits WHERE total_charge >= 0 AND total_charge <= 990", 0.5)
    assert second.status_code == 403
    assert "overlaps an earlier one" in second.json()["error"]["message"]


def test_a_refused_differencing_query_costs_nothing(client):
    run(client, "alice", "SELECT COUNT(*) FROM visits WHERE total_charge >= 0", 0.5)
    before = budget(client, "alice")["rho_spent"]
    assert run(client, "alice", "SELECT COUNT(*) FROM visits WHERE total_charge <= 990", 0.5).status_code == 403
    assert budget(client, "alice")["rho_spent"] == before


def test_averaging_the_same_question_returns_one_answer_and_one_charge(client):
    answers, charges = set(), 0.0
    for _ in range(15):
        body = run(client, "alice", SUM, 0.1).json()
        answers.add(json.dumps(body["result"]))
        charges += body["rho_charged"]
    assert len(answers) == 1, "repeats produced fresh noise to average"
    assert charges == pytest.approx(0.1)


def test_tautology_rewrites_cannot_buy_fresh_noise(client):
    """`total_charge <= 1000000 + k` selects the same rows with a different fingerprint."""
    answers = set()
    for k in range(6):
        r = run(client, "alice", f"SELECT SUM(total_charge) FROM visits WHERE total_charge <= {1000000 + k}", 0.1)
        if r.status_code == 200:
            answers.add(json.dumps(r.json()["result"]))
        else:
            assert r.status_code == 403
    assert len(answers) <= 1, f"{len(answers)} independent noise draws for one question"


def test_budget_exhaustion_stops_the_analyst_at_the_allowance(client):
    refused = False
    for dept in range(1, 40):
        r = run(client, "frank", f"SELECT COUNT(*) FROM visits WHERE dept_id = {dept} AND total_charge > {dept * 11}", 0.08)
        if r.status_code == 429:
            refused = True
            break
    assert refused, "frank (allowance 0.2) was never cut off"
    spent = budget(client, "frank")
    assert spent["rho_spent"] <= spent["rho_total"] + 1e-9
    assert client.get("/api/dp/ledger/verify/", headers=auth(client, "frank")).json()["ok"] is True


def test_the_ledger_verifies_after_a_mixed_workload(client):
    for sql in (COUNT, SUM, "SELECT COUNT(*) FROM visits WHERE dept_id = 1", "DROP TABLE visits"):
        run(client, "alice", sql, 0.1)
    verdict = client.get("/api/dp/ledger/verify/", headers=auth(client, "alice")).json()
    assert verdict["ok"] is True


# ------------------------------------------------------------------ injection and bypass
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM visits; DROP TABLE visits",
        "SELECT COUNT(*) FROM visits WHERE admission_type = 'x' OR 1=1",
        "SELECT * FROM visits",
        "SELECT patient_id FROM visits",
        "SELECT COUNT(*) FROM visits UNION SELECT COUNT(*) FROM patients",
        "SELECT (SELECT COUNT(*) FROM patients) FROM visits",
        "INSERT INTO visits VALUES (1)",
        "COPY visits TO '/tmp/x'",
    ],
)
def test_sql_injection_and_non_aggregate_sql_is_refused_and_harmless(client, sql):
    r = run(client, "alice", sql)
    assert r.status_code in (400, 403), r.text
    # The engine is still up, and the table is intact and uncharged for the refusal.
    assert budget(client, "alice")["rho_spent"] == 0.0
    assert run(client, "alice").status_code == 200


@pytest.mark.parametrize(
    "sql, filtered",
    [
        # The doubled quotes stay inside one string literal: it compares against the text
        # `x' OR '1'='1`, matches nothing, and widens no predicate (one Filter, no OR branch).
        ("SELECT COUNT(*) FROM visits WHERE admission_type = 'x'' OR ''1''=''1'", True),
        # A comment is not a way to smuggle a predicate: it is just a plain COUNT(*).
        ("SELECT COUNT(*) FROM visits -- WHERE patient_id = 1", False),
    ],
)
def test_injection_that_the_parser_neutralises_is_treated_as_plain_data(client, sql, filtered):
    r = run(client, "alice", sql, 0.05)
    assert r.status_code == 200, r.text
    plan = client.post("/api/dp/explain/", headers=auth(client, "alice"), json={"sql": sql}).json()
    assert ("Filter" in plan["physical"]) is filtered
    assert "patient_id" not in plan["logical_text"]
    assert budget(client, "alice")["rho_spent"] == pytest.approx(0.05)  # charged as one ordinary query


def test_a_column_the_role_was_never_granted_is_refused(client):
    for col in ("patient_id", "visit_id", "attending_staff_id"):
        r = run(client, "alice", f"SELECT COUNT(*) FROM visits WHERE {col} = 3")
        assert r.status_code == 403, col
        r = run(client, "alice", f"SELECT SUM({col}) FROM visits")
        assert r.status_code in (400, 403), col


def test_a_principal_without_a_budget_account_cannot_query(client):
    assert run(client, "carol").status_code == 403


def test_revoking_a_grant_takes_effect_immediately(client):
    dba = auth(client, "dave")
    r = client.put("/api/policy/grants/", headers=dba,
                   json={"role": "dp_analyst", "table": "visits", "column": "dept_id", "granted": False})
    assert r.status_code == 200, r.text
    assert run(client, "alice", "SELECT COUNT(*) FROM visits WHERE dept_id = 1").status_code == 403


# ------------------------------------------------------------------ authentication
def _claims(client, user):
    return jwt.get_unverified_claims(login(client, user)["access"])


def test_a_token_signed_with_the_wrong_key_is_refused(client):
    claims = _claims(client, "alice")
    forged = jwt.encode(claims, "x" * 40, algorithm="HS256")
    r = client.post("/api/dp/query/", headers={"Authorization": f"Bearer {forged}"}, json={"sql": COUNT, "rho": 0.05})
    assert r.status_code == 401


def test_editing_a_valid_token_payload_invalidates_the_signature(client):
    token = login(client, "alice")["access"]
    head, payload, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = _claims(client, "dave")["sub"]
    forged_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    r = client.get("/api/policy/grid/", headers={"Authorization": f"Bearer {head}.{forged_payload}.{sig}"})
    assert r.status_code == 401


def test_a_refresh_token_cannot_be_used_as_an_access_token(client):
    refresh = login(client, "alice")["refresh"]
    r = client.post("/api/dp/query/", headers={"Authorization": f"Bearer {refresh}"}, json={"sql": COUNT, "rho": 0.05})
    assert r.status_code == 401


def test_an_analyst_cannot_use_admin_endpoints(client):
    h = auth(client, "alice")
    assert client.get("/api/policy/grid/", headers=h).status_code == 403
    r = client.put("/api/policy/grants/", headers=h,
                   json={"role": "dp_analyst", "table": "visits", "column": "patient_id", "granted": True})
    assert r.status_code == 403
    assert run(client, "alice", "SELECT COUNT(*) FROM visits WHERE patient_id = 3").status_code == 403


def test_an_analyst_cannot_read_another_principals_budget_or_audit(client):
    run(client, "frank")
    mine = client.get("/api/queries/", headers=auth(client, "alice")).json()
    assert all(row["principal_name"] == "alice" for row in mine["results"])
    assert budget(client, "alice")["principal_name"] == "alice"
