"""Separation of duty is enforced by Postgres, not just by the API; and responses are time-bucketed.

Whoever can read the per-patient ledger (the dba) must not be able to run queries, because that
ledger shows exactly which patients a query touched.
"""
import math

import pytest
from django.db import connection, transaction
from django.db.utils import DatabaseError

from accounts.models import Principal, PrincipalRole, Role
from accounts.roles import JOB_ROLES
from privacy import service
from privacy.service import padded_duration


def principal(name):
    return Principal.objects.create_user(username=name, password="S3cure-pass-123")


def give(user, role):
    PrincipalRole.objects.create(principal=user, role=Role.objects.get(role_name=role))


QUERY_ROLES = ["dp_analyst", "auditor", "researcher", "data_scientist", "compliance_officer", "privacy_officer"]


# --- the database refuses the combination, from either direction ----------------------------------
@pytest.mark.parametrize("query_role", QUERY_ROLES)
def test_dba_then_query_role_is_refused(db, query_role):
    user = principal(f"sod_a_{query_role}")
    give(user, "dba")
    with pytest.raises(DatabaseError, match="separation of duty"), transaction.atomic():
        give(user, query_role)
    assert not user.has_role(query_role)


@pytest.mark.parametrize("query_role", QUERY_ROLES)
def test_query_role_then_dba_is_refused(db, query_role):
    user = principal(f"sod_b_{query_role}")
    give(user, query_role)
    with pytest.raises(DatabaseError, match="separation of duty"), transaction.atomic():
        give(user, "dba")
    assert not user.has_role("dba")


@pytest.mark.parametrize("pair", [["dba"], ["dp_analyst", "clinician"], ["auditor", "clinician"],
                                  ["researcher", "nurse"], ["dba", "clinician"]])
def test_other_combinations_are_allowed(db, pair):
    user = principal("sod_ok_" + "_".join(pair))
    for role in pair:
        give(user, role)
    assert {r for r in pair if user.has_role(r)} == set(pair)


def test_swapping_roles_works(db):
    user = principal("sod_swap")
    give(user, "dp_analyst")
    PrincipalRole.objects.filter(principal=user).delete()
    give(user, "dba")
    assert user.has_role("dba") and not user.has_role("dp_analyst")


def test_the_trigger_lists_every_query_carrying_role(db):
    """If roles.py gains a query role the trigger does not know, this fails: add a migration."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_get_functiondef('privguard_separation_of_duty'::regproc)")
        definition = cur.fetchone()[0]
    expected = {"dp_analyst", "auditor"} | {n for n, (base, _) in JOB_ROLES.items() if base in ("dp_analyst", "auditor")}
    listed = {name for name in expected if f"'{name}'" in definition}
    assert listed == expected
    for name, (base, _) in JOB_ROLES.items():
        if base == "clinician":
            assert f"'{name}'" not in definition  # clinical roles do not run DP queries


# --- time buckets --------------------------------------------------------------------------------
@pytest.mark.parametrize("elapsed, expected", [(0.0, 0.15), (0.05, 0.15), (0.15, 0.15), (0.151, 0.3), (0.3, 0.3),
                                               (0.31, 0.6), (0.6, 0.6), (0.61, 1.2), (5.0, 9.6)])
def test_padded_duration_rounds_up_to_a_doubling_of_the_floor(elapsed, expected):
    assert padded_duration(elapsed, 0.15) == pytest.approx(expected)


def test_padding_never_shortens_and_never_exceeds_double():
    for i in range(1, 400):
        elapsed = i * 0.01
        padded = padded_duration(elapsed, 0.15)
        assert elapsed <= padded + 1e-12 and (elapsed <= 0.15 or padded < 2 * elapsed + 1e-12)
    assert padded_duration(3.0, 0) == 0.0  # no floor configured: no padding


def test_two_different_amounts_of_work_in_one_bucket_look_the_same():
    assert padded_duration(0.17, 0.15) == padded_duration(0.29, 0.15)
    assert padded_duration(0.17, 0.15) != padded_duration(0.31, 0.15)


def test_the_query_wrapper_pads_with_the_configured_floor(db, settings, monkeypatch):
    settings.PRIVGUARD_MIN_RESPONSE_MS = 150
    seen = []
    monkeypatch.setattr(service, "padded_duration", lambda elapsed, floor: seen.append(floor) or 0.0)
    monkeypatch.setattr(service, "_run_private_query", lambda *a, **k: {"status": "executed"})
    user = principal("pad_user")
    give(user, "dp_analyst")
    service.run_private_query(user, "nowhere", "SELECT COUNT(*) FROM visits")
    assert seen == [0.15]


# --- a query needs a query role, not just a budget account -------------------------------------------
@pytest.mark.parametrize("role, allowed", [("dp_analyst", True), ("auditor", True), ("researcher", True),
                                           ("compliance_officer", True), ("clinician", False), ("nurse", False)])
def test_only_query_roles_may_run_queries(account, visits, settings, role, allowed):
    from budget.models import BudgetAccount
    from catalog.models import SysTable
    settings.PRIVGUARD_MIN_RESPONSE_MS = 0
    SysTable.objects.create(db=account.db, table_name="visits")
    user = principal(f"q_{role}")
    give(user, role)
    BudgetAccount.objects.create(principal=user, db=account.db, rho_total=5.0, delta_target=1e-6)
    call = lambda: service.run_private_query(user, account.db.db_name, "SELECT COUNT(*) FROM visits", rho_req=0.05)
    if allowed:
        assert call()["status"] == "executed"
    else:
        with pytest.raises(service.DPQueryError, match="no query role"):
            call()


def test_an_account_with_no_roles_at_all_cannot_query(account, visits):
    from budget.models import BudgetAccount
    user = principal("roleless")
    BudgetAccount.objects.create(principal=user, db=account.db, rho_total=5.0, delta_target=1e-6)
    with pytest.raises(service.DPQueryError, match="no query role"):
        service.run_private_query(user, account.db.db_name, "SELECT COUNT(*) FROM visits", rho_req=0.05)


# --- the audit command -----------------------------------------------------------------------------
def test_the_audit_reports_a_conflict_that_predates_the_trigger(db):
    from io import StringIO
    from django.core.management import call_command
    from django.core.management.base import CommandError
    out = StringIO()
    call_command("audit_separation_of_duty", stdout=out)
    assert "holds" in out.getvalue()
    user = principal("legacy_mix")
    give(user, "dp_analyst")
    with connection.cursor() as cur:  # how old or restored data could contain a conflict
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # Postgres refuses ALTER while FK checks wait
        cur.execute("ALTER TABLE sys_principal_role DISABLE TRIGGER trg_separation_of_duty")
    give(user, "dba")
    with pytest.raises(CommandError, match="1 account"):
        call_command("audit_separation_of_duty", stderr=StringIO())
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cur.execute("ALTER TABLE sys_principal_role ENABLE TRIGGER trg_separation_of_duty")
