from .test_dp import COUNT, query


def run_many(client, headers, n):
    for i in range(n):
        # Distinct predicates, so none is a cache hit.
        sql = f"SELECT COUNT(*) FROM visits WHERE dept_id = {i}"
        assert query(client, headers, sql=sql, rho=0.01).status_code == 200


def test_the_audit_log_is_paginated_in_the_drf_shape(client, as_user):
    h = as_user("alice")
    run_many(client, h, 25)
    first = client.get("/api/queries/", headers=h).json()
    assert set(first) == {"count", "next", "previous", "results"}
    assert first["count"] == 25
    assert len(first["results"]) == 20
    assert first["previous"] is None
    assert "page=2" in first["next"]

    second = client.get("/api/queries/", headers=h, params={"page": 2}).json()
    assert len(second["results"]) == 5
    assert second["next"] is None
    assert "page=1" in second["previous"]


def test_default_order_is_newest_first_and_can_be_reversed(client, as_user):
    h = as_user("alice")
    run_many(client, h, 3)
    newest = client.get("/api/queries/", headers=h).json()["results"]
    oldest = client.get("/api/queries/", headers=h, params={"ordering": "submitted_at"}).json()["results"]
    assert [r["query_id"] for r in newest] == [3, 2, 1]
    assert [r["query_id"] for r in oldest] == [1, 2, 3]


def test_status_filter_and_search(client, as_user):
    h = as_user("alice")
    query(client, h)
    query(client, h)  # cache hit
    assert client.get("/api/queries/", headers=h, params={"status": "cache_hit"}).json()["count"] == 1
    assert client.get("/api/queries/", headers=h, params={"search": "count"}).json()["count"] == 2
    assert client.get("/api/queries/", headers=h, params={"search": "zzz"}).json()["count"] == 0


def test_analysts_see_only_their_own_rows_and_never_latency(client, as_user):
    query(client, as_user("alice"))
    query(client, as_user("erin"))
    rows = client.get("/api/queries/", headers=as_user("alice")).json()["results"]
    assert [r["principal_name"] for r in rows] == ["alice"]
    assert "latency_ms" not in rows[0]
    assert rows[0]["sql_text"] == COUNT


def test_auditors_see_everyone_including_latency(client, as_user):
    query(client, as_user("alice"))
    query(client, as_user("frank"))
    rows = client.get("/api/queries/", headers=as_user("erin")).json()["results"]
    assert {r["principal_name"] for r in rows} == {"alice", "frank"}
    assert all("latency_ms" in r for r in rows)


def test_analysts_cannot_sort_by_the_hidden_latency_column(client, as_user):
    r = client.get("/api/queries/", headers=as_user("alice"), params={"ordering": "latency_ms"})
    assert r.status_code == 400
    ok = client.get("/api/queries/", headers=as_user("erin"), params={"ordering": "latency_ms"})
    assert ok.status_code == 200


def test_audit_requires_authentication(client):
    assert client.get("/api/queries/").status_code == 401
