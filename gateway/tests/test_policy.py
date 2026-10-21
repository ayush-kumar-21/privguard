"""The policy grid endpoints (stub engine). The real-engine equivalents are in test_pg_engine.py."""
import pytest


def grid(client, headers):
    return client.get("/api/policy/grid/", headers=headers)


def put(client, headers, **body):
    return client.put("/api/policy/grants/", headers=headers, json=body)


def test_the_grid_lists_every_fact_column_with_a_cell_per_role(client, as_user):
    r = grid(client, as_user("dave"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["roles"] == ["auditor", "clinician", "dba", "dp_analyst"]
    cols = {c["column"]: c for c in body["columns"]}
    assert len(cols) == 8
    assert set(cols["dept_id"]["grants"]) == set(body["roles"])
    assert cols["dept_id"]["grants"]["dp_analyst"] is True
    assert cols["dept_id"]["grants"]["clinician"] is False
    assert cols["total_charge"]["lower_clamp"] == 0.0
    assert cols["patient_id"]["access"] == "denied"


def test_only_dba_and_auditors_may_read_the_grid(client, as_user):
    assert grid(client, as_user("erin")).status_code == 200
    r = grid(client, as_user("alice"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "42501"
    assert client.get("/api/policy/grid/").status_code == 401


def test_a_dba_can_toggle_a_grant_and_the_grid_reflects_it(client, as_user):
    h = as_user("dave")
    r = put(client, h, role="clinician", table="visits", column="dept_id", granted=True)
    assert r.status_code == 200, r.text
    cell = next(c for c in r.json()["columns"] if c["column"] == "dept_id")
    assert cell["grants"]["clinician"] is True
    r = put(client, h, role="clinician", table="visits", column="dept_id", granted=False)
    assert next(c for c in r.json()["columns"] if c["column"] == "dept_id")["grants"]["clinician"] is False


@pytest.mark.parametrize("who", ["alice", "erin", "carol"])
def test_everyone_but_a_dba_is_refused_a_change(client, as_user, who):
    r = put(client, as_user(who), role="dp_analyst", table="visits", column="dept_id", granted=False)
    assert r.status_code == 403


def test_grant_bodies_are_validated(client, as_user):
    h = as_user("dave")
    for bad in (
        {"role": "dp_analyst'; DROP", "table": "visits", "column": "dept_id", "granted": True},
        {"role": "dp_analyst", "table": "visits", "column": "dept id", "granted": True},
        {"role": "dp_analyst", "table": "visits", "column": "dept_id"},
    ):
        assert put(client, h, **bad).status_code == 400
    unknown = put(client, h, role="ghost", table="visits", column="dept_id", granted=True)
    assert unknown.status_code == 404
    assert put(client, h, role="dp_analyst", table="visits", column="nope", granted=True).status_code == 404
