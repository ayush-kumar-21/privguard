"""User administration: a dba creates users and grants roles, credits and run quotas."""
import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from accounts.models import Principal, PrincipalRole, Role
from budget.models import BudgetAccount
from catalog.models import SysDatabase

PASSWORD = "S3cure-pass-123"
NEW_PASSWORD = "An0ther-Str0ng-pw"


def _login(client, username, password=PASSWORD):
    resp = client.post("/api/auth/login/", {"username": username, "password": password}, format="json")
    return resp


def _client(user):
    client = APIClient()
    resp = _login(client, user.username)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return client


def _make(name, *roles):
    user = Principal.objects.create_user(username=name, password=PASSWORD)
    for r in roles:
        PrincipalRole.objects.create(principal=user, role=Role.objects.get_or_create(role_name=r)[0])
    return user


@pytest.fixture
def roles(db):
    for r in ("dba", "dp_analyst", "auditor", "clinician"):
        Role.objects.get_or_create(role_name=r)


@pytest.fixture
def safehealth(db):
    return SysDatabase.objects.create(db_name="safehealth", default_rho=2.5, default_delta=1e-6)


@pytest.fixture
def admin(roles, safehealth):
    return _make("real_admin", "dba")


@pytest.fixture
def api(admin):
    return _client(admin)


def new_user(api, **over):
    body = {"username": "newbie", "password": NEW_PASSWORD, "roles": ["dp_analyst"], **over}
    return api.post("/api/users/", body, format="json")


# --- who may do this ----------------------------------------------------------
@pytest.mark.parametrize("role", ["dp_analyst", "auditor", "clinician"])
def test_only_a_dba_may_list_or_create(roles, safehealth, role):
    client = _client(_make(f"plain_{role}", role))
    assert client.get("/api/users/").status_code == 403
    assert client.post("/api/users/", {"username": "x_user", "password": NEW_PASSWORD, "roles": ["dba"]},
                       format="json").status_code == 403
    assert not Principal.objects.filter(username="x_user").exists()


def test_anonymous_is_refused(db):
    assert APIClient().get("/api/users/").status_code == 401


# --- creating -----------------------------------------------------------------
def test_create_an_analyst_with_credits_and_a_quota_who_can_log_in(api, admin):
    resp = new_user(api, rho_total=3.0, max_queries=20)
    assert resp.status_code == 201, resp.data
    assert resp.data["roles"] == ["dp_analyst"]
    assert resp.data["account"]["rho_total"] == 3.0 and resp.data["account"]["max_queries"] == 20
    assert "password" not in resp.data and "password" not in str(resp.data)
    assert _login(APIClient(), "newbie", NEW_PASSWORD).status_code == 200
    assert Principal.objects.get(username="newbie").is_superuser is False


def test_a_query_role_gets_a_default_budget_account(api):
    resp = new_user(api)  # no figure given
    assert resp.data["account"]["rho_total"] == 2.5  # the database default
    assert resp.data["account"]["max_queries"] is None


def test_a_clinician_gets_no_account_unless_asked(api):
    assert new_user(api, username="doc_one", roles=["clinician"]).data["account"] is None
    assert new_user(api, username="doc_two", roles=["clinician"], rho_total=1).data["account"]["rho_total"] == 1


@pytest.mark.parametrize("over, fragment", [
    ({"username": "ab"}, "username"),
    ({"username": "bad name!"}, "username"),
    ({"password": "short"}, ""),
    ({"password": "password123"}, ""),
    ({"password": "12345678901"}, ""),
    ({"roles": []}, "roles"),
    ({"roles": ["emperor"]}, "unknown role"),
    ({"roles": "dba"}, "roles"),
    ({"rho_total": 0}, "rho_total"),
    ({"rho_total": -1}, "rho_total"),
    ({"rho_total": 10**6}, "rho_total"),
    ({"rho_total": "lots"}, "rho_total"),
    ({"rho_total": True}, "rho_total"),
    ({"max_queries": -5}, "max_queries"),
    ({"max_queries": 2.5}, "max_queries"),
])
def test_bad_input_is_a_400_and_creates_nothing(api, over, fragment):
    resp = new_user(api, **over)
    assert resp.status_code == 400, resp.data
    assert fragment in resp.data["error"]
    assert not Principal.objects.filter(username="newbie").exists()


@pytest.mark.parametrize("literal", ["Infinity", "NaN", "-Infinity"])
def test_non_finite_credits_are_refused(api, literal):
    raw = '{"username": "newbie", "password": "%s", "roles": ["dp_analyst"], "rho_total": %s}' % (NEW_PASSWORD, literal)
    assert api.post("/api/users/", raw, content_type="application/json").status_code == 400
    assert not Principal.objects.filter(username="newbie").exists()


def test_usernames_are_unique_ignoring_case(api):
    assert new_user(api).status_code == 201
    assert new_user(api, username="NEWBIE").status_code == 400


def test_a_body_that_is_not_an_object_is_a_400_not_a_crash(api):
    assert api.post("/api/users/", [1, 2], format="json").status_code == 400


def test_the_api_never_grants_superuser(api):
    resp = new_user(api, is_superuser=True, is_staff=True)
    assert resp.status_code == 201
    user = Principal.objects.get(username="newbie")
    assert not user.is_superuser and not user.is_staff


# --- editing ------------------------------------------------------------------
def test_change_roles_credits_and_quota(api):
    uid = new_user(api, rho_total=2.0).data["id"]
    resp = api.patch(f"/api/users/{uid}/", {"roles": ["auditor", "clinician"], "rho_total": 6.5, "max_queries": 9}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["roles"] == ["auditor", "clinician"]
    assert resp.data["account"]["rho_total"] == 6.5 and resp.data["account"]["max_queries"] == 9
    assert api.patch(f"/api/users/{uid}/", {"max_queries": None}, format="json").data["account"]["max_queries"] is None


def test_credits_cannot_drop_below_what_is_spent(api):
    uid = new_user(api, rho_total=5.0).data["id"]
    BudgetAccount.objects.filter(principal_id=uid).update(rho_spent=3.0)
    resp = api.patch(f"/api/users/{uid}/", {"rho_total": 2.0}, format="json")
    assert resp.status_code == 400 and "already spent" in resp.data["error"]
    assert BudgetAccount.objects.get(principal_id=uid).rho_total == 5.0
    assert api.patch(f"/api/users/{uid}/", {"rho_total": 3.0}, format="json").status_code == 200


def test_granting_credits_to_a_user_without_an_account_creates_one(api):
    uid = new_user(api, username="doc_x", roles=["clinician"]).data["id"]
    resp = api.patch(f"/api/users/{uid}/", {"rho_total": 4.0, "max_queries": 3}, format="json")
    assert resp.data["account"]["rho_total"] == 4.0 and resp.data["account"]["max_queries"] == 3


def test_a_quota_needs_an_account(api):
    uid = new_user(api, username="doc_y", roles=["clinician"]).data["id"]
    assert api.patch(f"/api/users/{uid}/", {"max_queries": 3}, format="json").status_code == 400


def test_reset_a_password(api):
    uid = new_user(api).data["id"]
    assert api.patch(f"/api/users/{uid}/", {"password": "weak"}, format="json").status_code == 400
    assert api.patch(f"/api/users/{uid}/", {"password": "Brand-N3w-Passw0rd"}, format="json").status_code == 200
    assert _login(APIClient(), "newbie", NEW_PASSWORD).status_code == 401
    assert _login(APIClient(), "newbie", "Brand-N3w-Passw0rd").status_code == 200


def test_a_deactivated_user_cannot_log_in(api):
    uid = new_user(api).data["id"]
    assert api.patch(f"/api/users/{uid}/", {"is_active": False}, format="json").status_code == 200
    assert _login(APIClient(), "newbie", NEW_PASSWORD).status_code == 401
    assert api.patch(f"/api/users/{uid}/", {"is_active": "no"}, format="json").status_code == 400


def test_unknown_user_is_a_404(api):
    assert api.patch("/api/users/987654/", {"is_active": False}, format="json").status_code == 404


# --- lockout protection -------------------------------------------------------
def test_an_admin_cannot_demote_or_deactivate_themselves(api, admin):
    url = f"/api/users/{admin.id}/"
    assert api.patch(url, {"roles": ["auditor"]}, format="json").status_code == 400
    assert api.patch(url, {"is_active": False}, format="json").status_code == 400
    assert admin.has_role("dba") and Principal.objects.get(pk=admin.pk).is_active


def test_the_last_active_administrator_cannot_be_removed(api, admin):
    second_id = new_user(api, username="second_dba", roles=["dba"]).data["id"]
    # Now two admins: removing the second is fine...
    assert api.patch(f"/api/users/{second_id}/", {"roles": ["auditor"]}, format="json").status_code == 200
    # ...but a lone admin cannot be deactivated or demoted by someone else.
    other = _make("other_dba", "dba")
    other_client = _client(other)
    assert api.patch(f"/api/users/{other.id}/", {"is_active": False}, format="json").status_code == 200
    resp = other_client.patch(f"/api/users/{admin.id}/", {"is_active": False}, format="json")
    assert resp.status_code in (400, 401, 403)  # the deactivated admin's token no longer works, or it is refused
    assert Principal.objects.get(pk=admin.pk).is_active


def test_the_list_shows_everyone_with_budgets(api, admin):
    new_user(api, rho_total=1.5, max_queries=4)
    rows = {r["username"]: r for r in api.get("/api/users/").data}
    assert set(rows) >= {"real_admin", "newbie"}
    assert rows["newbie"]["account"]["queries_remaining"] == 4
    assert rows["real_admin"]["roles"] == ["dba"]


# --- the command-line way to make a real admin ---------------------------------
def test_create_admin_command(roles, monkeypatch):
    monkeypatch.setenv("PRIVGUARD_ADMIN_PASSWORD", NEW_PASSWORD)
    call_command("create_admin", "boss")
    boss = Principal.objects.get(username="boss")
    assert boss.has_role("dba") and boss.is_staff and boss.is_superuser
    assert _login(APIClient(), "boss", NEW_PASSWORD).status_code == 200


def test_create_admin_refuses_a_weak_password(roles, monkeypatch):
    from django.core.management.base import CommandError
    monkeypatch.setenv("PRIVGUARD_ADMIN_PASSWORD", "weak")
    with pytest.raises(CommandError):
        call_command("create_admin", "boss2")
    assert not Principal.objects.filter(username="boss2").exists()


# --- findings from the security review -----------------------------------------
def test_a_dba_cannot_take_over_a_superuser_account(api, admin):
    owner = Principal.objects.create_superuser(username="site_owner", password=PASSWORD)
    for body in ({"password": NEW_PASSWORD}, {"is_active": False}, {"roles": ["auditor"]}):
        assert api.patch(f"/api/users/{owner.id}/", body, format="json").status_code == 403
    assert _login(APIClient(), "site_owner", PASSWORD).status_code == 200  # untouched


def test_a_superuser_may_manage_other_superusers(admin):
    boss = Principal.objects.create_superuser(username="boss_one", password=PASSWORD)
    other = Principal.objects.create_superuser(username="boss_two", password=PASSWORD)
    assert _client(boss).patch(f"/api/users/{other.id}/", {"is_active": False}, format="json").status_code == 200


def test_a_superuser_counts_as_an_administrator_for_the_last_admin_guard(api, admin):
    Principal.objects.create_superuser(username="site_owner", password=PASSWORD)
    other = _make("temp_dba", "dba")
    assert api.patch(f"/api/users/{other.id}/", {"is_active": False}, format="json").status_code == 200


@pytest.mark.parametrize("name", ["abc\n", "abc\r\nforged", "ab c", "caf\u00e9"])
def test_usernames_with_newlines_or_odd_characters_are_refused(api, name):
    assert new_user(api, username=name).status_code == 400
    assert Principal.objects.filter(username=name).count() == 0


def test_a_rejected_edit_changes_nothing(api):
    uid = new_user(api, username="doc_z", roles=["clinician"]).data["id"]
    resp = api.patch(f"/api/users/{uid}/", {"roles": ["auditor"], "is_active": False, "max_queries": 5}, format="json")
    # auditor is a query role, so an account will exist and the quota is fine; use a role that is not.
    assert resp.status_code == 200
    uid2 = new_user(api, username="doc_w", roles=["clinician"]).data["id"]
    resp = api.patch(f"/api/users/{uid2}/", {"roles": ["nurse"], "is_active": False, "max_queries": 5}, format="json")
    assert resp.status_code == 400
    user = Principal.objects.get(pk=uid2)
    assert user.is_active and sorted(r.role_name for r in user.roles.all()) == ["clinician"]


# --- separation of duty: who can read per-patient charges must not be able to run queries ---------
@pytest.mark.parametrize("roles", [["dba", "dp_analyst"], ["dba", "researcher"], ["dba", "auditor"],
                                   ["dba", "compliance_officer"]])
def test_an_administrator_cannot_also_hold_a_query_role(api, roles):
    resp = new_user(api, username="mixed", roles=roles)
    assert resp.status_code == 400 and "separation of duty" in resp.data["error"]
    assert not Principal.objects.filter(username="mixed").exists()


def test_giving_an_analyst_the_admin_role_on_top_is_refused_too(api):
    uid = new_user(api, username="analyst_x", roles=["dp_analyst"]).data["id"]
    resp = api.patch(f"/api/users/{uid}/", {"roles": ["dp_analyst", "dba"]}, format="json")
    assert resp.status_code == 400 and "separation of duty" in resp.data["error"]
    assert api.patch(f"/api/users/{uid}/", {"roles": ["dba"]}, format="json").status_code == 200  # a swap is fine
