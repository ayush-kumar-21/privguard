"""Run quotas: an administrator caps how many times an analyst may run a query.

The quota is enforced by one conditional UPDATE per query, so it is exact under concurrency and
an administrator's change applies to the very next run.
"""
import pytest
from rest_framework.test import APIClient

from accounts.models import Principal, PrincipalRole, Role
from audit.models import SysQuery
from budget.models import BudgetAccount
from catalog.models import SysTable
from privacy.service import _take_run, run_private_query
from privacy.sql import DPQueryError

PASSWORD = "S3cure-pass-123"
COUNT = "SELECT COUNT(*) FROM visits"


def count_where(n):
    return f"SELECT COUNT(*) FROM visits WHERE total_charge > {n}"


@pytest.fixture
def ready(analyst, account, visits, settings):
    settings.PRIVGUARD_MIN_RESPONSE_MS = 0
    SysTable.objects.create(db=account.db, table_name="visits")
    return account


def run(account, sql=COUNT, rho=0.05):
    return run_private_query(account.principal, account.db.db_name, sql, rho_req=rho)


def reload(account):
    account.refresh_from_db()
    return account


def test_no_quota_means_unlimited_but_runs_are_still_counted(ready):
    for n in range(4):
        assert run(ready, count_where(n))["status"] == "executed"
    ready = reload(ready)
    assert ready.queries_used == 4  # usage is always tracked, so an administrator can see it
    assert ready.queries_remaining is None


def test_the_quota_is_exact_and_the_refusal_costs_nothing(ready):
    ready.max_queries = 2
    ready.save()
    first = run(ready, count_where(1))
    second = run(ready, count_where(2))
    assert (first["status"], second["status"]) == ("executed", "executed")
    assert second["budget"]["queries_remaining"] == 0

    spent = reload(ready).rho_spent
    third = run(ready, count_where(3))
    assert third["status"] == "rejected_budget" and third["reason"] == "quota"
    assert third["result"] == [] and third["rho_charged"] == 0.0
    ready = reload(ready)
    assert ready.queries_used == 2 and ready.rho_spent == spent
    assert SysQuery.objects.filter(status="rejected_budget").count() == 1  # visible to monitoring


def test_a_cached_repeat_uses_a_run_too(ready):
    ready.max_queries = 2
    ready.save()
    assert run(ready)["status"] == "executed"
    assert run(ready)["status"] == "cache_hit"
    assert run(ready)["reason"] == "quota"


def test_a_query_that_produces_no_answer_gives_the_run_back(ready):
    ready.max_queries = 1
    ready.save()
    with pytest.raises(DPQueryError):  # SUM needs a clamp policy the test database lacks
        run(ready, "SELECT SUM(total_charge) FROM visits")
    assert reload(ready).queries_used == 0
    assert run(ready)["status"] == "executed"  # the run was still there


def test_an_administrator_change_applies_to_the_next_query(ready):
    ready.max_queries = 1
    ready.save()
    assert run(ready, count_where(1))["status"] == "executed"
    assert run(ready, count_where(2))["reason"] == "quota"

    BudgetAccount.objects.filter(pk=ready.pk).update(max_queries=3)  # raised while running
    assert run(ready, count_where(2))["status"] == "executed"

    BudgetAccount.objects.filter(pk=ready.pk).update(max_queries=0)  # cut off at once
    assert run(ready, count_where(3))["reason"] == "quota"
    assert reload(ready).queries_remaining == 0  # never negative, even below what was used


def test_take_run_hands_out_exactly_the_quota(analyst, account):
    BudgetAccount.objects.filter(pk=account.pk).update(max_queries=5)
    taken = [_take_run(analyst, account.db.db_name) for _ in range(12)]
    assert sum(t is not None for t in taken) == 5
    assert BudgetAccount.objects.get(pk=account.pk).queries_used == 5
    # Exactness under concurrency comes from the single conditional UPDATE; it is exercised live
    # against the running API (12 parallel requests, quota 5) rather than in a rolled-back test.


# --- the administrator's API -------------------------------------------------
def _client(user):
    client = APIClient()
    resp = client.post("/api/auth/login/", {"username": user.username, "password": PASSWORD}, format="json")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return client


@pytest.fixture
def dba(db):
    user = Principal.objects.create_user(username="quota_dba", password=PASSWORD)
    PrincipalRole.objects.create(principal=user, role=Role.objects.get_or_create(role_name="dba")[0])
    return user


@pytest.fixture
def analyst_client(account):
    account.principal.set_password(PASSWORD)
    account.principal.save()
    return _client(account.principal)


def test_a_dba_sets_and_clears_a_quota(account, dba):
    client, url = _client(dba), f"/api/budget-accounts/{account.pk}/quota/"
    body = client.post(url, {"max_queries": 25}, format="json")
    assert body.status_code == 200 and body.data["max_queries"] == 25
    assert body.data["queries_remaining"] == 25
    assert client.post(url, {"max_queries": None}, format="json").data["queries_remaining"] is None
    BudgetAccount.objects.filter(pk=account.pk).update(queries_used=9)
    assert client.post(url, {"max_queries": 10, "reset_used": True}, format="json").data["queries_used"] == 0


@pytest.mark.parametrize("bad", [-1, "ten", 2.5, True, [3]])
def test_a_quota_must_be_a_whole_number_or_null(account, dba, bad):
    resp = _client(dba).post(f"/api/budget-accounts/{account.pk}/quota/", {"max_queries": bad}, format="json")
    assert resp.status_code == 400
    assert reload(account).max_queries is None


def test_an_analyst_cannot_change_their_own_quota(account, analyst_client):
    resp = analyst_client.post(f"/api/budget-accounts/{account.pk}/quota/", {"max_queries": 9999}, format="json")
    assert resp.status_code == 403
    assert reload(account).max_queries is None


def test_an_unknown_account_is_a_404_not_a_crash(dba):
    assert _client(dba).post("/api/budget-accounts/987654/quota/", {"max_queries": 3}, format="json").status_code == 404


def test_the_budget_endpoint_shows_the_quota_live(account, analyst_client):
    BudgetAccount.objects.filter(pk=account.pk).update(max_queries=4, queries_used=1)
    data = analyst_client.get("/api/dp/budget/", {"db": account.db.db_name}).data
    assert (data["max_queries"], data["queries_used"], data["queries_remaining"]) == (4, 1, 3)
    BudgetAccount.objects.filter(pk=account.pk).update(max_queries=2)
    assert analyst_client.get("/api/dp/budget/", {"db": account.db.db_name}).data["queries_remaining"] == 1
