"""API-level tests: authentication, RBAC, pagination/search/filter, error envelope.

These cover the rubric's Section 4 (search, filtering, pagination, exception
handling) and Section 5 (login/logout, token handling, `/me`, role-based API
access, single Users table).
"""
import pytest
from django.conf import settings
from rest_framework.test import APIClient

from accounts.models import Principal, PrincipalRole, Role
from safehealth.models import Region

PASSWORD = "S3cure-pass-123"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def dba(db):
    user = Principal.objects.create_user(username="dba_user", password=PASSWORD)
    role, _ = Role.objects.get_or_create(role_name="dba")
    PrincipalRole.objects.create(principal=user, role=role)
    return user


@pytest.fixture
def plain_user(db):
    return Principal.objects.create_user(username="plain_user", password=PASSWORD)


def login(client, username):
    resp = client.post(
        "/api/auth/login/", {"username": username, "password": PASSWORD}, format="json"
    )
    assert resp.status_code == 200, resp.content
    return resp.data


def authed(client, username):
    tokens = login(client, username)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client, tokens


@pytest.mark.django_db
class TestAuthentication:
    def test_login_me_logout_cycle(self, client, plain_user):
        client, tokens = authed(client, "plain_user")

        me = client.get("/api/auth/me/")
        assert me.status_code == 200
        assert me.data["username"] == "plain_user"
        assert "password" not in me.data

        out = client.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
        assert out.status_code == 200

        # A blacklisted refresh token can no longer mint access tokens.
        again = APIClient().post(
            "/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        assert again.status_code == 401

    def test_bad_credentials_rejected(self, client, plain_user):
        resp = client.post(
            "/api/auth/login/", {"username": "plain_user", "password": "nope"}, format="json"
        )
        assert resp.status_code == 401
        assert resp.data["error"]["code"]

    def test_anonymous_cannot_read_data(self, client):
        assert client.get("/api/regions/").status_code == 401

    def test_passwords_are_argon2_hashed(self, plain_user):
        assert plain_user.password.startswith("argon2")

    def test_register_validates_password_length(self, client):
        resp = client.post(
            "/api/auth/register/", {"username": "short", "password": "abc"}, format="json"
        )
        assert resp.status_code == 400
        assert "password" in resp.data["error"]["details"]


@pytest.mark.django_db
class TestSingleUsersTable:
    def test_one_users_table_with_role_mapping(self, dba):
        assert settings.AUTH_USER_MODEL == "accounts.Principal"
        assert Principal._meta.db_table == "sys_principal"
        assert dba.has_role("dba")
        assert not dba.has_role("auditor")


@pytest.mark.django_db
class TestRoleBasedAccess:
    def test_non_dba_cannot_write(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        resp = client.post(
            "/api/regions/", {"region_name": "North", "state_code": "NR"}, format="json"
        )
        assert resp.status_code == 403
        assert resp.data["error"]["code"] == "permission_denied"

    def test_non_dba_can_read(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        assert client.get("/api/regions/").status_code == 200

    def test_dba_full_crud(self, client, dba):
        client, _ = authed(client, "dba_user")
        created = client.post(
            "/api/regions/", {"region_name": "North", "state_code": "NR"}, format="json"
        )
        assert created.status_code == 201
        rid = created.data["region_id"]

        updated = client.patch(f"/api/regions/{rid}/", {"state_code": "NN"}, format="json")
        assert updated.status_code == 200
        assert updated.data["state_code"] == "NN"

        assert client.delete(f"/api/regions/{rid}/").status_code == 204
        assert client.get(f"/api/regions/{rid}/").status_code == 404


@pytest.mark.django_db
class TestListFeatures:
    @pytest.fixture(autouse=True)
    def regions(self, db):
        Region.objects.bulk_create(
            Region(region_name=f"Region {i:02d}", state_code="AA" if i % 2 else "BB")
            for i in range(25)
        )

    def test_pagination(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        first = client.get("/api/regions/")
        assert first.data["count"] == 25
        assert len(first.data["results"]) == 20
        second = client.get("/api/regions/?page=2")
        assert len(second.data["results"]) == 5

    def test_search(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        resp = client.get("/api/regions/?search=Region 07")
        assert resp.data["count"] == 1

    def test_filter(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        resp = client.get("/api/regions/?state_code=AA")
        assert resp.data["count"] == 12

    def test_ordering(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        resp = client.get("/api/regions/?ordering=-region_name")
        assert resp.data["results"][0]["region_name"] == "Region 24"


@pytest.mark.django_db
class TestErrorEnvelope:
    def test_validation_error_shape(self, client, dba):
        client, _ = authed(client, "dba_user")
        resp = client.post("/api/regions/", {"region_name": ""}, format="json")
        assert resp.status_code == 400
        err = resp.data["error"]
        assert err["code"] == "validation_error"
        assert "region_name" in err["details"]

    def test_duplicate_is_client_error_not_500(self, client, dba):
        client, _ = authed(client, "dba_user")
        body = {"region_name": "Dup", "state_code": "DP"}
        assert client.post("/api/regions/", body, format="json").status_code == 201
        resp = client.post("/api/regions/", body, format="json")
        assert resp.status_code in (400, 409)
        assert "error" in resp.data

    def test_not_found_shape(self, client, plain_user):
        client, _ = authed(client, "plain_user")
        resp = client.get("/api/regions/999999/")
        assert resp.status_code == 404
        assert resp.data["error"]["code"] == "not_found"
