import base64
import json

import pytest

from privguard_gateway.security import encode_token, hash_password
from privguard_gateway.settings import ConfigError, Settings

from .conftest import PASSWORD, SECRET, auth, login


def test_login_returns_an_access_and_a_refresh_token(client):
    tokens = login(client, "alice")
    assert set(tokens) == {"access", "refresh"}
    assert tokens["access"] != tokens["refresh"]


def test_wrong_password_and_unknown_user_look_identical(client):
    bad_pw = client.post("/api/auth/login/", json={"username": "alice", "password": "nope"})
    no_user = client.post("/api/auth/login/", json={"username": "mallory", "password": "nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()
    body = bad_pw.json()["error"]
    assert body["code"] == "not_authenticated"
    assert body["message"]


def test_me_requires_a_token_and_never_exposes_the_hash(client):
    assert client.get("/api/auth/me/").status_code == 401
    me = client.get("/api/auth/me/", headers=auth(client, "erin")).json()
    assert me["username"] == "erin"
    assert me["roles"] == ["auditor"]
    assert "password" not in json.dumps(me).lower()


def test_passwords_are_stored_as_argon2id(users):
    assert users.by_username("alice").password_hash.startswith("$argon2id$")
    assert hash_password("x") != hash_password("x"), "hashes must be salted"


def test_refresh_rotates_and_a_replayed_token_is_refused(client):
    first = login(client, "alice")
    second = client.post("/api/auth/refresh/", json={"refresh": first["refresh"]})
    assert second.status_code == 200
    assert second.json()["refresh"] != first["refresh"]
    replay = client.post("/api/auth/refresh/", json={"refresh": first["refresh"]})
    assert replay.status_code == 401
    # The rotated token still works.
    assert client.post("/api/auth/refresh/", json={"refresh": second.json()["refresh"]}).status_code == 200


def test_logout_invalidates_the_refresh_token_and_is_idempotent(client):
    tokens = login(client, "alice")
    assert client.post("/api/auth/logout/", json={"refresh": tokens["refresh"]}).status_code == 200
    assert client.post("/api/auth/refresh/", json={"refresh": tokens["refresh"]}).status_code == 401
    assert client.post("/api/auth/logout/", json={"refresh": tokens["refresh"]}).status_code == 200
    assert client.post("/api/auth/logout/", json={"refresh": "garbage"}).status_code == 200


def test_token_kinds_are_not_interchangeable(client):
    tokens = login(client, "alice")
    as_bearer = {"Authorization": f"Bearer {tokens['refresh']}"}
    assert client.get("/api/auth/me/", headers=as_bearer).status_code == 401
    assert client.post("/api/auth/refresh/", json={"refresh": tokens["access"]}).status_code == 401


def test_expired_and_forged_tokens_are_refused(client):
    expired = encode_token(SECRET, "1", "access", -10)
    forged = encode_token("another-secret-" + "y" * 32, "1", "access", 600)
    for token in (expired, forged, "not-a-jwt", ""):
        r = client.get("/api/auth/me/", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token


def test_an_unsigned_alg_none_token_is_refused(client):
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': '1', 'type': 'access', 'jti': 'a', 'exp': 9999999999})}."
    r = client.get("/api/auth/me/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_a_token_for_a_principal_that_does_not_exist_is_refused(client):
    ghost = encode_token(SECRET, "999", "access", 600)
    r = client.get("/api/auth/me/", headers={"Authorization": f"Bearer {ghost}"})
    assert r.status_code == 401


def test_login_validates_its_body(client):
    r = client.post("/api/auth/login/", json={"username": "alice"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_the_secret_is_mandatory_and_must_be_long(monkeypatch):
    with pytest.raises(ConfigError):
        Settings(jwt_secret="short")
    with pytest.raises(ConfigError):
        Settings.from_env({})
    with pytest.raises(ConfigError):
        Settings.from_env({"PRIVGUARD_GATEWAY_SECRET": "dev-insecure-change-me"})
    ok = Settings.from_env(
        {"PRIVGUARD_GATEWAY_SECRET": "z" * 40, "PRIVGUARD_GATEWAY_DEMO_USERS": "1"}
    )
    assert ok.demo_users is True
    assert Settings.from_env({"PRIVGUARD_GATEWAY_SECRET": "z" * 40}).demo_users is False


def test_demo_password_constant_matches_the_legacy_seed():
    assert PASSWORD == "privguard123"
