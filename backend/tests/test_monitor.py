"""The monitor: staff-only live pages, the demo-mode key, and the request log behind them."""
import pytest
from django.test import Client
from rest_framework.test import APIClient

from accounts.models import Principal
from monitor.middleware import LOG

PASSWORD = "S3cure-pass-123"
KEY = "k" * 40


@pytest.fixture(autouse=True)
def clean_log():
    LOG.clear()
    yield
    LOG.clear()


@pytest.fixture
def staff(db):
    return Principal.objects.create_user(username="ops_staff", password=PASSWORD, is_staff=True)


@pytest.fixture
def plain(db):
    return Principal.objects.create_user(username="ops_plain", password=PASSWORD)


def staff_client(user):
    client = Client()
    client.force_login(user)
    return client


PAGES = ["backend", "database", "login"]


# --- who may look ---------------------------------------------------------------
@pytest.mark.parametrize("name", PAGES)
def test_anonymous_pages_redirect_to_the_admin_login(db, name):
    resp = Client().get(f"/monitor/{name}/")
    assert resp.status_code == 302 and resp["Location"].startswith("/admin/login/?next=/monitor/")


@pytest.mark.parametrize("name", PAGES)
def test_anonymous_feeds_are_a_401_not_data(db, name):
    resp = Client().get(f"/monitor/feed/{name}/")
    assert resp.status_code == 401 and "error" in resp.json()


def test_a_signed_in_user_who_is_not_staff_is_refused(plain):
    client = staff_client(plain)
    assert client.get("/monitor/backend/").status_code == 302
    assert client.get("/monitor/feed/database/").status_code == 401


@pytest.mark.parametrize("name", PAGES)
def test_staff_see_every_page_and_feed(staff, name):
    client = staff_client(staff)
    page = client.get(f"/monitor/{name}/")
    assert page.status_code == 200 and f'data-page="{name}"'.encode() in page.content
    feed = client.get(f"/monitor/feed/{name}/")
    assert feed.status_code == 200 and feed["Content-Type"] == "application/json"


def test_unknown_pages_are_404_and_the_root_goes_to_the_backend_page(staff):
    client = staff_client(staff)
    assert client.get("/monitor/nonsense/").status_code == 404
    assert client.get("/monitor/").status_code == 302


# --- the demo-mode key -----------------------------------------------------------
@pytest.fixture
def demo(settings):
    settings.DEBUG = True
    settings.PRIVGUARD_MONITOR_KEY = KEY


def test_the_key_opens_a_read_only_monitor_session(db, demo):
    client = Client()
    resp = client.get(f"/monitor/enter/?key={KEY}&next=/monitor/database/")
    assert resp.status_code == 302 and resp["Location"] == "/monitor/database/"
    assert client.get("/monitor/feed/database/").status_code == 200
    assert client.get("/admin/").status_code == 302  # it is not an admin login


@pytest.mark.parametrize("key", ["", "wrong", KEY[:-1], KEY + "x"])
def test_a_wrong_key_is_a_404_and_grants_nothing(db, demo, key):
    client = Client()
    assert client.get("/monitor/enter/", {"key": key}).status_code == 404
    assert client.get("/monitor/feed/backend/").status_code == 401


def test_the_key_does_nothing_outside_demo_mode(db, settings):
    settings.DEBUG = False
    settings.PRIVGUARD_MONITOR_KEY = KEY
    assert Client().get(f"/monitor/enter/?key={KEY}").status_code == 404


def test_no_key_configured_means_no_key_login(db, settings):
    settings.DEBUG = True
    settings.PRIVGUARD_MONITOR_KEY = ""
    assert Client().get("/monitor/enter/?key=").status_code == 404


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/admin/", "monitor/backend/", "/monitor/../admin/"])
def test_the_entry_link_never_redirects_off_the_monitor(db, demo, target):
    resp = Client().get("/monitor/enter/", {"key": KEY, "next": target})
    assert resp.status_code == 302
    assert resp["Location"] == "/monitor/backend/"


# --- the request log ---------------------------------------------------------------
def _login(client, username, password):
    return client.post("/api/auth/login/", {"username": username, "password": password}, format="json")


def test_requests_are_logged_with_the_signed_in_user(plain):
    api = APIClient()
    token = _login(api, "ops_plain", PASSWORD).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api.get("/api/auth/me/?token=secret-in-query").status_code == 200
    entry = [e for e in LOG if e["path"] == "/api/auth/me/"][-1]
    assert entry["user"] == "ops_plain" and entry["status"] == 200 and entry["method"] == "GET"
    assert "secret-in-query" not in str(list(LOG))  # query strings are never kept


def test_a_login_attempt_logs_the_username_but_never_the_password(plain):
    ok = _login(APIClient(), "ops_plain", PASSWORD)
    bad = _login(APIClient(), "ops_plain", "Wr0ng-Passw0rd-xyz")
    assert ok.status_code == 200 and bad.status_code == 401  # the body was still readable by the view
    logins = [e for e in LOG if e["path"] == "/api/auth/login/"]
    assert [(e["status"], e["user"]) for e in logins] == [(200, "ops_plain"), (401, "ops_plain")]
    dump = str(list(LOG))
    assert PASSWORD not in dump and "Wr0ng-Passw0rd-xyz" not in dump


@pytest.mark.parametrize("name", ["bad name", "x" * 80, "<script>alert(1)</script>", "a\nb"])
def test_odd_usernames_are_not_stored_verbatim(db, name):
    _login(APIClient(), name, "whatever-123")
    assert [e["user"] for e in LOG if e["path"] == "/api/auth/login/"] == ["?"]


def test_monitor_and_static_requests_are_not_logged(staff):
    staff_client(staff).get("/monitor/feed/backend/")
    assert len(LOG) == 0


def test_the_log_is_bounded(db):
    client = APIClient()
    for _ in range(LOG.maxlen + 25):
        client.get("/api/health/")
    assert len(LOG) == LOG.maxlen


# --- the feeds ---------------------------------------------------------------------
def test_backend_feed_summarises_the_log(staff, plain):
    api = APIClient()
    _login(api, "ops_plain", PASSWORD)
    api.get("/api/definitely-missing/")
    data = staff_client(staff).get("/monitor/feed/backend/").json()
    assert data["requests_total"] >= 2 and data["errors_4xx"] >= 1 and data["errors_5xx"] == 0
    assert data["db_ping_ms"] >= 0 and data["uptime_s"] >= 0
    assert data["recent"][0]["path"] == "/api/definitely-missing/"  # newest first
    assert set(data["recent"][0]) == {"t", "method", "path", "status", "ms", "user"}


def test_login_feed_counts_each_step(staff, plain):
    for _ in range(2):
        _login(APIClient(), "ops_plain", PASSWORD)
    _login(APIClient(), "ops_plain", "nope-nope-nope-1")
    data = staff_client(staff).get("/monitor/feed/login/").json()
    assert data["steps"]["login_ok"] == 2 and data["steps"]["login_failed"] == 1
    assert {u["user"] for u in data["users"]} >= {"ops_staff", "ops_plain"}
    assert data["users"][0].keys() >= {"user", "active", "roles", "last_seen"}
    assert all("password" not in str(u) for u in data["users"])


def test_database_feed_lists_databases_tables_and_budgets(staff, account):
    data = staff_client(staff).get("/monitor/feed/database/").json()
    assert data["engine"].startswith("PostgreSQL")
    assert any(d["name"] == data["current"] for d in data["databases"])
    tables = {t["name"]: t for t in data["tables"]}
    assert tables["sys_principal"]["system"] is True and tables["sys_principal"]["rows"] >= 1
    assert tables["patients"]["system"] is False
    assert [a["user"] for a in data["accounts"]] == ["test_analyst"]
