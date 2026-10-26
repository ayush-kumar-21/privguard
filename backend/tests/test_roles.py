"""Enterprise job roles: each carries exactly one base role's powers, and nothing more."""
import pytest
from rest_framework.test import APIClient

from accounts.models import Principal, PrincipalRole, Role
from accounts.roles import BASE_ROLES, JOB_ROLES, carried_role, effective_role_names
from budget.models import BudgetAccount
from catalog.models import SysDatabase

PASSWORD = "S3cure-pass-123"
BASES = set(BASE_ROLES)


def _make(name, *roles):
    user = Principal.objects.create_user(username=name, password=PASSWORD)
    for r in roles:
        PrincipalRole.objects.create(principal=user, role=Role.objects.get(role_name=r))
    return user


def _client(user):
    client = APIClient()
    resp = client.post("/api/auth/login/", {"username": user.username, "password": PASSWORD}, format="json")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return client


# --- the catalogue itself ------------------------------------------------------
def test_the_catalogue_is_consistent():
    assert not set(JOB_ROLES) & BASES                       # no job role shadows a base role
    assert all(carries in BASES for carries, _ in JOB_ROLES.values())
    assert all(carries != "dba" for carries, _ in JOB_ROLES.values())  # admin is granted, never implied
    assert all(text for _, text in JOB_ROLES.values())


def test_the_migration_created_every_role_with_a_description(db):
    names = set(Role.objects.values_list("role_name", flat=True))
    assert names >= BASES | set(JOB_ROLES)
    assert not Role.objects.filter(role_name__in=BASES, description__isnull=True).exists()


def test_effective_names_accept_a_one_shot_iterator():
    assert effective_role_names(iter(["researcher", "nurse"])) == {"researcher", "dp_analyst", "nurse", "clinician"}
    assert effective_role_names([]) == set()
    assert carried_role("dba") is None and carried_role("nobody") is None


# --- what each job role can and cannot be ----------------------------------------
@pytest.mark.parametrize("role", sorted(JOB_ROLES))
def test_a_job_role_carries_its_base_role_and_nothing_else(db, role):
    user = _make(f"u_{role}", role)
    base = JOB_ROLES[role][0]
    assert user.has_role(role) and user.has_role(base)
    assert not user.has_role("dba")
    assert {r for r in BASES if user.has_role(r)} == {base}


def test_me_reports_effective_roles_and_what_was_assigned(db):
    body = _client(_make("rae", "researcher")).get("/api/auth/me/").data
    assert body["roles"] == ["dp_analyst", "researcher"]
    assert body["assigned_roles"] == ["researcher"]


def test_roles_endpoint_names_the_role_each_one_carries(db):
    admin = _make("root_admin", "dba")
    rows = {r["role_name"]: r for r in _client(admin).get("/api/roles/").data["results"]}
    assert rows["researcher"]["carries"] == "dp_analyst" and rows["dba"]["carries"] is None
    assert rows["nurse"]["description"]


# --- real enforcement, through the API -------------------------------------------
@pytest.fixture
def world(db):
    safehealth = SysDatabase.objects.create(db_name="safehealth", default_rho=2.0, default_delta=1e-6)
    people = {n: _make(n, n) for n in ("researcher", "compliance_officer", "nurse", "privacy_officer")}
    for who in ("researcher", "privacy_officer"):
        BudgetAccount.objects.create(principal=people[who], db=safehealth, rho_total=3.0, delta_target=1e-6)
    return people


def test_an_analyst_job_role_sees_only_its_own_account(world):
    seen = _client(world["researcher"]).get("/api/budget-accounts/").data["results"]
    assert [a["principal_name"] for a in seen] == ["researcher"]
    assert _client(world["researcher"]).get("/api/users/").status_code == 403


def test_an_oversight_job_role_sees_every_account_but_cannot_change_anything(world):
    client = _client(world["compliance_officer"])
    names = {a["principal_name"] for a in client.get("/api/budget-accounts/").data["results"]}
    assert names == {"researcher", "privacy_officer"}
    account = BudgetAccount.objects.first()
    assert client.post(f"/api/budget-accounts/{account.pk}/quota/", {"max_queries": 1}, format="json").status_code == 403
    assert client.get("/api/users/").status_code == 403


def test_a_clinical_job_role_may_write_clinical_data_but_not_administer(world):
    nurse = _client(world["nurse"])
    # An empty body is a validation error (400), which means the permission check passed.
    assert nurse.post("/api/patients/", {}, format="json").status_code == 400
    assert nurse.get("/api/users/").status_code == 403
    assert _client(world["researcher"]).post("/api/patients/", {}, format="json").status_code == 403


def test_the_administrator_can_hand_out_job_roles_and_budgets_follow(world):
    admin = _client(_make("boss_admin", "dba"))
    made = admin.post("/api/users/", {"username": "epi_one", "password": "Zebra-Quartz-5821",
                                      "roles": ["epidemiologist"]}, format="json")
    assert made.status_code == 201, made.data
    assert made.data["roles"] == ["epidemiologist"]          # assigned, not the carried base role
    assert made.data["account"]["rho_total"] == 2.0           # a query role gets a default budget
    clin = admin.post("/api/users/", {"username": "nurse_one", "password": "Zebra-Quartz-5821",
                                      "roles": ["nurse"]}, format="json")
    assert clin.data["account"] is None                       # a clinical role does not
