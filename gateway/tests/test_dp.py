import pytest

COUNT = "SELECT COUNT(*) FROM visits"


def query(client, headers, sql=COUNT, rho=0.05, **extra):
    return client.post(
        "/api/dp/query/", headers=headers, json={"db": "safehealth", "sql": sql, "rho": rho, **extra}
    )


def test_query_needs_authentication(client):
    assert client.post("/api/dp/query/", json={"sql": COUNT}).status_code == 401


def test_a_count_query_returns_the_dp_response_shape(client, as_user):
    r = query(client, as_user("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "executed"
    assert body["result"] == [{"value": 196}]
    assert body["mechanism"] == "discrete_laplace"
    assert body["rho_charged"] == 0.05
    assert body["epsilon_charged"] > body["rho_charged"]
    assert body["budget"]["rho_spent"] == pytest.approx(0.05)
    assert body["budget"]["rho_remaining"] == pytest.approx(4.95)
    assert body["suppressed_groups"] == 0


def test_a_repeated_query_is_a_cache_hit_that_costs_nothing(client, as_user):
    h = as_user("alice")
    first = query(client, h).json()
    # Different spelling, same query: the stub's fingerprint ignores case and whitespace.
    again = query(client, h, sql="select   count(*)   from VISITS ;").json()
    assert again["status"] == "cache_hit"
    assert again["rho_charged"] == 0.0
    assert again["result"] == first["result"]
    assert again["budget"]["rho_spent"] == first["budget"]["rho_spent"]


def test_the_cache_is_per_principal(client, as_user):
    query(client, as_user("alice"))
    other = query(client, as_user("erin")).json()
    assert other["status"] == "executed"
    assert other["rho_charged"] == 0.05


def test_grouped_queries_return_group_keys_and_suppression(client, as_user):
    body = query(client, as_user("alice"), "SELECT COUNT(*) FROM visits GROUP BY admission_type").json()
    assert body["mechanism"] == "gaussian"
    assert {row["admission_type"] for row in body["result"]} == {"ELECTIVE", "EMERGENCY", "URGENT"}
    assert body["suppressed_groups"] == 1


def test_filters_in_the_supported_subset_are_accepted(client, as_user):
    sql = "SELECT SUM(total_charge) FROM visits WHERE dept_id = 2 AND total_charge > 500.5"
    r = query(client, as_user("alice"), sql)
    assert r.status_code == 200, r.text
    assert r.json()["mechanism"] == "laplace"


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE visits",
        "SELECT * FROM visits",
        "SELECT COUNT(admission_type) FROM visits",
        "SELECT COUNT(*) FROM patients",
        "SELECT COUNT(*) FROM visits WHERE dept_id = 1 OR dept_id = 2",
        "SELECT COUNT(*) FROM visits; DROP TABLE visits",
        "SELECT AVG(total_charge) FROM visits GROUP BY dept_id",
        "SELECT COUNT(*) FROM visits WHERE patient_id = 7",
    ],
)
def test_sql_outside_the_subset_is_a_parse_error(client, as_user, sql):
    r = query(client, as_user("alice"), sql)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "42601"
    assert err["message"]


def test_a_principal_without_a_budget_account_is_refused_by_policy(client, as_user):
    r = query(client, as_user("carol"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "42501"


def test_budget_exhaustion_is_refused_and_audited(client, as_user):
    h = as_user("frank")  # 0.2 rho
    assert query(client, h, rho=0.15).status_code == 200
    r = query(client, h, sql="SELECT AVG(total_charge) FROM visits", rho=0.15)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "53400"
    rejected = client.get("/api/queries/", headers=h, params={"status": "rejected_budget"}).json()
    assert rejected["count"] == 1
    # The refused query spent nothing.
    assert client.get("/api/dp/budget/", headers=h).json()["rho_spent"] == pytest.approx(0.15)


@pytest.mark.parametrize("rho", [0, -0.1, 99, "abc"])
def test_rho_is_validated(client, as_user, rho):
    r = query(client, as_user("alice"), rho=rho)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_oversized_and_empty_sql_are_refused(client, as_user):
    h = as_user("alice")
    assert query(client, h, sql="").status_code == 400
    assert query(client, h, sql="SELECT COUNT(*) FROM visits WHERE " + "a" * 5000).status_code == 400


def test_unknown_database_is_a_catalog_error(client, as_user):
    r = query(client, as_user("alice"), db="other")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "42P01"


def test_budget_endpoint(client, as_user):
    r = client.get("/api/dp/budget/", headers=as_user("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["principal_name"] == "alice"
    assert body["rho_total"] == 5.0
    assert body["rho_remaining"] == 5.0
    assert client.get("/api/dp/budget/", headers=as_user("carol")).status_code == 404


def test_ledger_verification_counts_entries(client, as_user):
    h = as_user("alice")
    assert client.get("/api/dp/ledger/verify/", headers=h).json() == {
        "ok": True,
        "entries": 0,
        "first_bad_entry": None,
    }
    query(client, h)
    assert client.get("/api/dp/ledger/verify/", headers=h).json()["entries"] == 2


def test_budget_accounts_are_scoped_to_the_caller_unless_privileged(client, as_user):
    own = client.get("/api/budget-accounts/", headers=as_user("alice")).json()
    assert [a["principal_name"] for a in own["results"]] == ["alice"]
    everyone = client.get("/api/budget-accounts/", headers=as_user("erin")).json()
    assert {a["principal_name"] for a in everyone["results"]} == {"alice", "frank", "erin"}


def test_healthz_needs_no_token(client):
    assert client.get("/api/healthz").json() == {"ok": True}


def test_unknown_routes_use_the_error_envelope(client):
    r = client.get("/api/nope/")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
