import pytest
from fastapi.testclient import TestClient

from privguard_gateway.app import create_app
from privguard_gateway.engine import StubEngine
from privguard_gateway.settings import Settings
from privguard_gateway.store import UserStore

SECRET = "test-secret-" + "x" * 32
PASSWORD = "privguard123"


@pytest.fixture(scope="session")
def users() -> UserStore:
    # argon2 is deliberately slow; hash the demo principals once for the whole session.
    return UserStore.with_demo_principals(PASSWORD)


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret=SECRET)


@pytest.fixture
def client(settings, users) -> TestClient:
    # A fresh stub engine per test so budgets and caches never leak between tests.
    return TestClient(create_app(settings, StubEngine(), users))


def login(client: TestClient, username: str) -> dict:
    r = client.post("/api/auth/login/", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def auth(client: TestClient, username: str) -> dict:
    return {"Authorization": f"Bearer {login(client, username)['access']}"}


@pytest.fixture
def as_user(client):
    return lambda name: auth(client, name)
