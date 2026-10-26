"""Per-patient privacy budgets: charged only for contributors, exhaustion leaves a patient out,
refunds on failure, tamper-evident per-patient chains, and the auditor API.

Fixtures from conftest: `visits` makes 6 patients. Patient 0 has 7 ELECTIVE visits only; patients
1-5 have one ELECTIVE and one URGENT visit each.
"""
import pytest
from django.db import connection, transaction
from django.db.utils import DatabaseError, IntegrityError
from rest_framework.test import APIClient

from accounts.models import Principal, PrincipalRole, Role
from audit.models import SysQuery
from budget.models import BudgetAccount, PatientBudget, PatientLedger
from catalog.models import SysDatabase, SysTable
from privacy import service
from privacy.service import Policy, _bounded_rows, _charge_patients, _refund_patients, run_private_query
from privacy.sql import parse
from safehealth.models import Patient

PASSWORD = "S3cure-pass-123"
URGENT = "SELECT COUNT(*) FROM visits WHERE admission_type = 'URGENT'"
ELECTIVE = "SELECT COUNT(*) FROM visits WHERE admission_type = 'ELECTIVE'"


@pytest.fixture
def ready(analyst, account, visits, settings):
    settings.PRIVGUARD_MIN_RESPONSE_MS = 0
    SysTable.objects.create(db=account.db, table_name="visits")
    return account


def run(account, sql, rho=0.05):
    return run_private_query(account.principal, account.db.db_name, sql, rho_req=rho)


def spent(pid):
    return PatientBudget.objects.get(pk=pid).rho_spent


def patients():
    return sorted(Patient.objects.values_list("patient_id", flat=True))


def another_analyst(db_, name):
    user = Principal.objects.create_user(username=name, password=PASSWORD)
    PrincipalRole.objects.create(principal=user, role=Role.objects.get_or_create(role_name="dp_analyst")[0])
    acct = BudgetAccount.objects.create(principal=user, db=db_, rho_total=10.0, delta_target=1e-6)
    return acct


# --- the budget rows ----------------------------------------------------------------
def test_every_patient_gets_a_budget_row_from_the_trigger(visits):
    assert PatientBudget.objects.count() == Patient.objects.count() == 6
    assert set(PatientBudget.objects.values_list("rho_total", flat=True)) == {10.0}


def test_the_database_default_sets_a_new_patients_allowance(db):
    SysDatabase.objects.create(db_name="safehealth", default_rho=1.0, default_delta=1e-6,
                               default_patient_rho=3.5)
    from safehealth.models import Region
    region = Region.objects.create(region_name="R", state_code="RR")
    p = Patient.objects.create(region=region, mrn="m", birth_year=1990, sex="F", zip3="123",
                               insurance_type="p", enrolled_at="2025-01-01")
    assert PatientBudget.objects.get(pk=p.pk).rho_total == 3.5


def test_the_budget_constraints_hold(visits):
    pid = patients()[0]
    with pytest.raises(IntegrityError), transaction.atomic():
        PatientBudget.objects.filter(pk=pid).update(rho_spent=11.0)  # more than the allowance
    with pytest.raises(IntegrityError), transaction.atomic():
        PatientBudget.objects.filter(pk=pid).update(rho_total=0)


# --- charging ------------------------------------------------------------------------
def test_only_patients_who_contribute_are_charged(ready):
    first = patients()[0]  # ELECTIVE only: never in an URGENT query
    assert run(ready, URGENT)["status"] == "executed"
    charged = set(PatientLedger.objects.filter(op="CHARGE").values_list("patient_id", flat=True))
    assert charged == set(patients()) - {first}
    assert spent(first) == 0.0
    assert all(spent(p) == pytest.approx(0.05) for p in charged)


def test_a_cached_repeat_charges_nobody(ready):
    run(ready, URGENT)
    before = PatientLedger.objects.count()
    assert run(ready, URGENT)["status"] == "cache_hit"
    assert PatientLedger.objects.count() == before


def test_a_query_refused_before_it_runs_charges_nobody(ready):
    BudgetAccount.objects.filter(pk=ready.pk).update(rho_total=0.01)  # cannot afford rho 0.05
    assert run(ready, URGENT)["status"] == "rejected_budget"
    BudgetAccount.objects.filter(pk=ready.pk).update(rho_total=10.0, max_queries=0)  # quota spent
    assert run(ready, ELECTIVE)["reason"] == "quota"
    assert PatientLedger.objects.count() == 0


# --- exhaustion ------------------------------------------------------------------------
def test_a_patient_who_cannot_afford_the_query_is_left_out_and_not_charged(visits):
    a, b = patients()[:2]
    PatientBudget.objects.filter(pk=a).update(rho_total=0.04)
    assert _charge_patients(None, 0.05, [a, b]) == [a]
    assert spent(a) == 0.0 and spent(b) == pytest.approx(0.05)


def test_the_last_affordable_charge_marks_the_patient_exhausted(visits):
    a = patients()[0]
    PatientBudget.objects.filter(pk=a).update(rho_total=0.1)
    assert _charge_patients(None, 0.05, [a]) == [] and _charge_patients(None, 0.05, [a]) == []
    row = PatientBudget.objects.get(pk=a)
    assert row.rho_spent == pytest.approx(0.1) and row.status == "exhausted"
    assert _charge_patients(None, 0.05, [a]) == [a]  # now left out


def test_an_excluded_patients_rows_are_not_in_the_query(visits):
    first = patients()[0]
    spec = parse(ELECTIVE)
    open_policy = Policy(5, 3, "discrete_laplace", None, None)
    closed = Policy(5, 3, "discrete_laplace", None, None)
    closed.excluded = frozenset({first})
    with_p = set(_bounded_rows(spec, open_policy).values_list("patient_id", flat=True))
    without_p = set(_bounded_rows(spec, closed).values_list("patient_id", flat=True))
    assert first in with_p and first not in without_p and without_p == with_p - {first}


def test_the_service_leaves_an_exhausted_patient_out_end_to_end(ready):
    first = patients()[0]
    PatientBudget.objects.filter(pk=first).update(rho_total=0.001)
    assert run(ready, ELECTIVE)["status"] == "executed"
    assert not PatientLedger.objects.filter(patient_id=first).exists()
    assert PatientLedger.objects.filter(op="CHARGE").count() == 5


def test_a_listed_patient_with_no_budget_row_is_left_out(visits):
    a, b = patients()[:2]
    PatientBudget.objects.filter(pk=a).delete()
    assert _charge_patients(None, 0.05, [a, b]) == [a]  # fail closed
    assert spent(b) == pytest.approx(0.05)


def test_a_non_positive_charge_is_refused(visits):
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
        cur.execute("SELECT fn_charge_patients(NULL, 0, ARRAY[1])")


# --- collusion: the point of the feature --------------------------------------------------
def test_more_analyst_accounts_do_not_buy_more_patient_budget(ready):
    db_ = ready.db
    PatientBudget.objects.update(rho_total=0.1)  # room for exactly two 0.05 queries per patient
    accounts = [ready, another_analyst(db_, "colluder_b"), another_analyst(db_, "colluder_c")]
    for acct in accounts[:2]:
        assert run(acct, ELECTIVE)["status"] == "executed"  # separate analysts, separate answers
    assert all(spent(p) == pytest.approx(0.1) for p in patients())
    run(accounts[2], ELECTIVE)  # the third has a full analyst budget, but every patient is spent
    assert all(spent(p) == pytest.approx(0.1) for p in patients())  # nothing more was taken
    assert PatientLedger.objects.filter(op="CHARGE").count() == 12  # 6 patients x 2 queries


# --- failure and refunds -------------------------------------------------------------------
def test_a_failed_query_gives_every_charge_back_once(ready, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("executor failed")
    monkeypatch.setattr(service, "_execute_scalar", boom)
    with pytest.raises(RuntimeError):
        run(ready, URGENT)
    assert all(spent(p) == 0.0 for p in patients())
    entries = PatientLedger.objects.order_by("entry_id")
    assert entries.filter(op="CHARGE").count() == entries.filter(op="REFUND").count() == 5
    qid = SysQuery.objects.latest("query_id").query_id
    _refund_patients(qid)  # idempotent: a second refund adds nothing
    assert PatientLedger.objects.count() == 10
    assert BudgetAccount.objects.get(pk=ready.pk).rho_reserved == 0.0


# --- the chain ---------------------------------------------------------------------------------
def test_each_patients_entries_form_a_hash_chain(ready):
    run(ready, URGENT)
    run(ready, "SELECT COUNT(*) FROM visits WHERE admission_type = 'URGENT' AND total_charge > 1")
    pid = patients()[1]
    rows = list(PatientLedger.objects.filter(patient_id=pid).order_by("entry_id"))
    assert len(rows) == 2 and bytes(rows[0].prev_hash) == b"\x00" * 32
    assert bytes(rows[1].prev_hash) == bytes(rows[0].entry_hash)
    with connection.cursor() as cur:
        cur.execute("SELECT ok, entries, first_bad_entry FROM fn_verify_patient_chain(%s)", [pid])
        assert cur.fetchone() == (True, 2, None)


def settle_pending_checks():
    """Run Django's deferred foreign-key checks now; Postgres refuses ALTER/TRUNCATE while they wait."""
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_ledger_is_append_only(ready):
    run(ready, URGENT)
    settle_pending_checks()
    for sql in ("UPDATE sys_patient_ledger SET rho_delta = 0",
                "DELETE FROM sys_patient_ledger", "TRUNCATE sys_patient_ledger"):
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql)
    assert PatientLedger.objects.count() == 5


def test_tampering_with_a_charge_is_detected(ready):
    run(ready, URGENT)
    pid = patients()[1]
    entry = PatientLedger.objects.filter(patient_id=pid).get()
    settle_pending_checks()
    with connection.cursor() as cur:  # what a DBA with table ownership could do
        cur.execute("ALTER TABLE sys_patient_ledger DISABLE TRIGGER trg_patient_ledger_readonly")
        cur.execute("UPDATE sys_patient_ledger SET rho_delta = rho_delta / 2 WHERE entry_id = %s", [entry.entry_id])
        cur.execute("SELECT ok, first_bad_entry FROM fn_verify_patient_chain(%s)", [pid])
        assert cur.fetchone() == (False, entry.entry_id)
        cur.execute("ALTER TABLE sys_patient_ledger ENABLE TRIGGER trg_patient_ledger_readonly")


# --- the auditor API -----------------------------------------------------------------------------
def _client(name, *roles):
    user = Principal.objects.create_user(username=name, password=PASSWORD)
    for r in roles:
        PrincipalRole.objects.create(principal=user, role=Role.objects.get_or_create(role_name=r)[0])
    client = APIClient()
    token = client.post("/api/auth/login/", {"username": name, "password": PASSWORD}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.mark.parametrize("role, allowed", [("dba", True), ("auditor", False), ("compliance_officer", False),
                                           ("dp_analyst", False), ("clinician", False), ("researcher", False)])
def test_only_the_dba_can_read_patient_budgets(visits, role, allowed):
    """The rows show exactly which patients a query touched, so nobody who can run queries may read them."""
    client = _client(f"u_{role}", role)
    for url in ("/api/patient-budgets/", "/api/patient-budgets/summary/", f"/api/patient-budgets/{patients()[0]}/verify/"):
        assert (client.get(url).status_code == 200) is allowed, url
    assert APIClient().get("/api/patient-budgets/").status_code == 401


def test_a_query_cannot_be_used_to_read_who_matched(ready):
    """The attack: run a query, then read which patients were charged. Both halves are now closed."""
    ready.principal.save()
    auditor = _client("snoop", "auditor")  # an auditor holds a budget account and can run queries
    acct = another_analyst(ready.db, "snoop2")
    run(acct, URGENT)  # the query touched specific patients
    for url in ("/api/patient-budgets/", "/api/patient-budgets/summary/", "/api/patient-budgets/1/verify/"):
        assert auditor.get(url).status_code == 403, url


@pytest.mark.parametrize("make", ["role", "superuser"])
def test_administrators_cannot_run_queries(ready, make):
    admin = Principal.objects.create_user(username=f"boss_{make}", password=PASSWORD,
                                          is_superuser=(make == "superuser"))
    if make == "role":
        PrincipalRole.objects.create(principal=admin, role=Role.objects.get_or_create(role_name="dba")[0])
    BudgetAccount.objects.create(principal=admin, db=ready.db, rho_total=10.0, delta_target=1e-6)
    with pytest.raises(service.DPQueryError, match="separation of duty"):
        run_private_query(admin, ready.db.db_name, URGENT, rho_req=0.05)
    assert PatientLedger.objects.count() == 0 and BudgetAccount.objects.get(principal=admin).queries_used == 0


def test_the_list_summary_and_verify_endpoints(ready):
    run(ready, URGENT)
    PatientBudget.objects.filter(pk=patients()[1]).update(rho_total=0.05, status="exhausted")
    client = _client("dba_one", "dba")
    listing = client.get("/api/patient-budgets/").data
    assert listing["count"] == 6 and listing["results"][0]["rho_spent"] == pytest.approx(0.05)
    assert set(listing["results"][0]) == {"patient_id", "rho_total", "rho_spent", "rho_remaining",
                                          "status", "charges", "last_charged_at"}  # no identifiers
    assert client.get("/api/patient-budgets/?status=exhausted").data["count"] == 1
    summary = client.get("/api/patient-budgets/summary/").data
    assert (summary["patients"], summary["exhausted"], summary["charges"]) == (6, 1, 5)
    ok = client.get(f"/api/patient-budgets/{patients()[1]}/verify/").data
    assert ok["ok"] is True and ok["entries"] == 1
    assert client.get("/api/patient-budgets/999999/verify/").status_code == 404
