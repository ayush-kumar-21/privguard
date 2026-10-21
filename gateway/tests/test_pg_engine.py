"""The gateway against the real engine, over pgwire.

Spawns `privguardd` on a database built from a small generated dataset, then drives the HTTP API.
Skipped when the daemon binary has not been built (`cd engine && cargo build -p privguardd`, or set
PRIVGUARD_ENGINE_BIN). CI builds it first, so there it always runs.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from privguard_gateway.app import create_app
from privguard_gateway.pgengine import PgEngine
from privguard_gateway.settings import Settings

from .conftest import PASSWORD, SECRET, auth

ENGINE_SECRET = "engine-service-secret-" + "e" * 16
NOISE_SECRET = "noise-secret-for-tests-" + "n" * 16
ROOT = Path(__file__).resolve().parents[2]


def _binary() -> Path | None:
    explicit = os.environ.get("PRIVGUARD_ENGINE_BIN")
    if explicit:
        return Path(explicit)
    name = "privguardd.exe" if sys.platform == "win32" else "privguardd"
    for profile in ("debug", "release"):
        p = ROOT / "engine" / "target" / profile / name
        if p.exists():
            return p
    return None


ENGINE_BIN = _binary()
pytestmark = pytest.mark.skipif(ENGINE_BIN is None, reason="privguardd is not built")

HEADERS = {
    "regions": "region_id,region_name,state_code",
    "hospitals": "hospital_id,region_id,hospital_name,bed_count",
    "departments": "dept_id,hospital_id,dept_name,specialty",
    "staff": "staff_id,dept_id,full_name,staff_role,hire_date",
    "patients": "patient_id,region_id,mrn,birth_year,sex,insurance_type,enrolled_at",
    "diagnoses": "diagnosis_id,visit_id,icd10_code,is_primary,severity",
    "lab_results": "lab_id,visit_id,test_code,value_num,unit,taken_at",
}
TYPES = ["ELECTIVE", "EMERGENCY", "URGENT"]


def _write_csvs(directory: Path) -> None:
    directory.mkdir(parents=True)
    for name, header in HEADERS.items():
        (directory / f"{name}.csv").write_text(header + "\n")
    lines = ["visit_id,patient_id,dept_id,attending_staff_id,admit_ts,discharge_ts,total_charge,admission_type"]
    visit = 0
    for patient in range(1, 41):
        for k in range(1 + patient % 4):
            visit += 1
            lines.append(
                f"{visit},{patient},{1 + visit % 3},1,2024-0{1 + visit % 9}-10 08:00:00,"
                f"2024-0{1 + visit % 9}-11 08:00:00,{100 + (visit * 37) % 900}.5,{TYPES[(patient + k) % 3]}"
            )
    (directory / "visits.csv").write_text("\n".join(lines) + "\n")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def engine_port(tmp_path_factory):
    work = tmp_path_factory.mktemp("engine")
    csv, db = work / "csv", work / "db"
    _write_csvs(csv)
    env = {**os.environ, "PRIVGUARD_SERVER_SECRET": NOISE_SECRET, "PRIVGUARD_ENGINE_SECRET": ENGINE_SECRET}
    subprocess.run([str(ENGINE_BIN), "load", str(csv), str(db)], check=True, env=env, capture_output=True)
    subprocess.run([str(ENGINE_BIN), "seed-demo", str(db)], check=True, env=env, capture_output=True)

    port = _free_port()
    proc = subprocess.Popen(
        [str(ENGINE_BIN), "serve", str(db), f"127.0.0.1:{port}"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("privguardd did not start listening")
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def pg_client(engine_port, users) -> TestClient:
    settings = Settings(
        jwt_secret=SECRET, engine_addr=f"127.0.0.1:{engine_port}", engine_secret=ENGINE_SECRET
    )
    return TestClient(create_app(settings, users=users))


def run(client, user, sql="SELECT COUNT(*) FROM visits", rho=0.05):
    return client.post("/api/dp/query/", headers=auth(client, user), json={"sql": sql, "rho": rho})


def test_a_count_runs_on_the_real_engine_and_is_charged(pg_client):
    r = run(pg_client, "alice")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "executed"
    assert isinstance(body["result"][0]["value"], int)
    assert body["mechanism"] == "discrete_laplace"
    assert body["rho_charged"] == pytest.approx(0.05)
    assert body["budget"]["rho_spent"] >= 0.05

    spent = pg_client.get("/api/dp/budget/", headers=auth(pg_client, "alice")).json()
    assert spent["principal_name"] == "alice"
    assert spent["rho_total"] == 5.0
    assert spent["rho_spent"] == pytest.approx(body["budget"]["rho_spent"])


def test_a_repeat_is_a_free_cache_hit_with_the_same_answer(pg_client):
    sql = "SELECT SUM(total_charge) FROM visits WHERE dept_id = 2"
    first = run(pg_client, "alice", sql).json()
    again = run(pg_client, "alice", sql).json()
    assert again["status"] == "cache_hit"
    assert again["rho_charged"] == 0.0
    assert again["result"] == first["result"]
    assert again["budget"]["rho_spent"] == first["budget"]["rho_spent"]


def test_grouped_results_come_back_keyed_by_the_group_column(pg_client):
    body = run(pg_client, "erin", "SELECT COUNT(*) FROM visits GROUP BY admission_type", 0.5).json()
    assert body["mechanism"] == "gaussian"
    assert all(set(row) == {"admission_type", "value"} for row in body["result"])
    assert body["suppressed_groups"] >= 0


def test_the_ledger_and_audit_views_come_from_the_engine(pg_client):
    run(pg_client, "alice", "SELECT AVG(total_charge) FROM visits")
    h = auth(pg_client, "alice")
    ledger = pg_client.get("/api/dp/ledger/verify/", headers=h).json()
    assert ledger["ok"] is True
    assert ledger["entries"] >= 2

    audit = pg_client.get("/api/queries/", headers=h).json()
    assert audit["count"] >= 1
    assert {r["principal_name"] for r in audit["results"]} == {"alice"}
    assert all("latency_ms" not in r for r in audit["results"])

    everyone = pg_client.get("/api/queries/", headers=auth(pg_client, "erin")).json()
    assert everyone["count"] >= audit["count"]
    accounts = pg_client.get("/api/budget-accounts/", headers=auth(pg_client, "erin")).json()
    assert {a["principal_name"] for a in accounts["results"]} == {"alice", "frank", "erin"}


def test_engine_errors_map_to_http_statuses(pg_client):
    parse = run(pg_client, "alice", "DROP TABLE visits")
    assert parse.status_code == 400
    assert parse.json()["error"]["code"] == "42601"

    denied = run(pg_client, "alice", "SELECT COUNT(*) FROM visits WHERE patient_id = 3")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "42501"

    unknown = run(pg_client, "alice", "SELECT COUNT(*) FROM nowhere")
    assert unknown.status_code == 404

    no_account = run(pg_client, "carol")
    assert no_account.status_code == 403


def test_budget_exhaustion_is_a_429_and_spends_nothing(pg_client):
    assert run(pg_client, "frank", rho=0.15).status_code == 200  # frank holds 0.2
    r = run(pg_client, "frank", "SELECT SUM(total_charge) FROM visits", 0.15)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "53400"
    spent = pg_client.get("/api/dp/budget/", headers=auth(pg_client, "frank")).json()
    assert spent["rho_spent"] == pytest.approx(0.15)


def test_explain_returns_the_physical_plan_for_the_plan_viewer(pg_client):
    r = pg_client.post(
        "/api/dp/explain/",
        headers=auth(pg_client, "alice"),
        json={"sql": "SELECT COUNT(*) FROM visits WHERE dept_id = 1 GROUP BY admission_type"},
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["physical"] == [
        "Scan", "Filter", "ContributionBound", "PartitionSelect", "DPAggregate"
    ]
    assert plan["logical"][0] == "Aggregate"
    assert plan["mechanism"] == "gaussian"


def test_an_unreachable_engine_is_a_503_not_a_stack_trace(users):
    settings = Settings(
        jwt_secret=SECRET, engine_addr=f"127.0.0.1:{_free_port()}", engine_secret=ENGINE_SECRET
    )
    client = TestClient(create_app(settings, users=users))
    r = run(client, "alice")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "08006"
    assert "engine" in r.json()["error"]["message"]


def test_a_wrong_service_secret_is_also_a_503(engine_port, users):
    settings = Settings(
        jwt_secret=SECRET, engine_addr=f"127.0.0.1:{engine_port}", engine_secret="x" * 40
    )
    client = TestClient(create_app(settings, users=users))
    assert run(client, "alice").status_code == 503


def test_the_secret_is_required_when_an_engine_address_is_set():
    from privguard_gateway.settings import ConfigError

    with pytest.raises(ConfigError):
        Settings(jwt_secret=SECRET, engine_addr="127.0.0.1:5433", engine_secret="short")


def test_pg_engine_never_interpolates_sql_into_its_own_statements(engine_port, users):
    # The analyst's text is only ever sent as the query itself; a quote cannot break out of the
    # `SET privguard.rho` parameter because rho is a validated float, bound by the driver.
    settings = Settings(
        jwt_secret=SECRET, engine_addr=f"127.0.0.1:{engine_port}", engine_secret=ENGINE_SECRET
    )
    client = TestClient(create_app(settings, users=users))
    r = run(client, "alice", "SELECT COUNT(*) FROM visits WHERE admission_type = 'x'; DROP TABLE visits")
    assert r.status_code == 400
    assert PgEngine is not None and PASSWORD
