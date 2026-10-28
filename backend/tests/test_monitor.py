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


PAGES = ["backend", "database", "login", "design"]


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


def test_the_monitor_never_shows_which_patients_a_query_touched(staff, account, visits, settings):
    """Row counts of the patient ledger depend on how many patients matched; they must stay hidden."""
    from privacy.service import run_private_query
    from catalog.models import SysTable
    settings.PRIVGUARD_MIN_RESPONSE_MS = 0
    SysTable.objects.create(db=account.db, table_name="visits")
    run_private_query(account.principal, account.db.db_name, "SELECT COUNT(*) FROM visits WHERE admission_type = 'URGENT'", rho_req=0.05)
    data = staff_client(staff).get("/monitor/feed/database/").json()
    assert "patients" not in data
    tables = {t["name"]: t for t in data["tables"]}
    assert tables["sys_patient_ledger"]["rows"] is None
    assert tables["sys_patient_budget"]["rows"] == 6  # one per patient: no query changes it


# --- system design: the plan, parsed live ------------------------------------------------------
PLAN = """# Plan

## Resume point

> **Done: phases 0 and 1.**
> Next: phase 2.

## Phase 0 \u2014 Repo preparation

- [x] 0.1 first thing
- [x] 0.2 second thing

## Phase 1 \u2014 WK 1\u20132 \u00b7 Foundation

- [x] 1.1 done thing
- [ ] 1.2 **open** thing with `code`
- [-] 1.3 dropped on purpose

## Bug log

- [ ] **P0** this is a bug, not a phase task
"""


def test_the_plan_parser_counts_done_open_and_dropped():
    from monitor.views import parse_plan
    plan = parse_plan(PLAN)
    assert [p["id"] for p in plan["phases"]] == ["Phase 0", "Phase 1"]
    p0, p1 = plan["phases"]
    assert (p0["done"], p0["total"]) == (2, 2)
    assert (p1["done"], p1["total"]) == (1, 2)                      # the dropped task is not counted
    assert [i["state"] for i in p1["items"]] == ["done", "open", "dropped"]
    assert p1["items"][1]["text"] == "1.2 open thing with code"     # markdown noise removed
    assert plan["resume"] == ["Done: phases 0 and 1.", "Next: phase 2."]
    assert all("bug" not in i["text"] for p in plan["phases"] for i in p["items"])  # bug log is not a phase


def test_the_design_feed_reads_the_configured_plan(staff, settings, tmp_path):
    plan_file = tmp_path / "PLAN.md"
    plan_file.write_text(PLAN, encoding="utf-8")
    settings.PRIVGUARD_PLAN_FILE = str(plan_file)
    data = staff_client(staff).get("/monitor/feed/design/").json()
    assert data["available"] is True and len(data["phases"]) == 2 and data["phases"][1]["done"] == 1


def test_a_missing_plan_file_is_reported_not_a_crash(staff, settings):
    settings.PRIVGUARD_PLAN_FILE = "/nonexistent/PLAN.md"
    resp = staff_client(staff).get("/monitor/feed/design/")
    assert resp.status_code == 200 and resp.json() == {"phases": [], "resume": [], "available": False}


def test_the_real_plan_in_the_repo_parses(staff, settings):
    """The shipped IMPLEMENTATION_PLAN.md must stay readable by the page."""
    import pathlib
    real = pathlib.Path(__file__).resolve().parents[2] / "IMPLEMENTATION_PLAN.md"
    if not real.exists():
        pytest.skip("repo root not mounted (running inside the container)")
    from monitor.views import parse_plan
    plan = parse_plan(real.read_text(encoding="utf-8"))
    assert len(plan["phases"]) >= 6 and sum(p["total"] for p in plan["phases"]) > 40


def test_the_design_page_shows_the_architecture_and_is_staff_only(db, staff):
    assert Client().get("/monitor/design/").status_code == 302
    page = staff_client(staff).get("/monitor/design/").content.decode()
    assert "Architecture: what runs in this demo" in page and "<svg" in page
    for part in ("Django REST API", "PostgreSQL", "React UI", "Research appendix"):
        assert part in page
    assert "Architecture:" not in staff_client(staff).get("/monitor/backend/").content.decode()


# --- the database browser: schema, views and values ---------------------------------------------------
@pytest.fixture
def root(db):
    return Principal.objects.create_superuser(username="ops_root", password=PASSWORD)


def feed(user, url):
    return staff_client(user).get(url)


def test_the_browser_feeds_need_a_sign_in(db):
    assert Client().get("/monitor/feed/schema/").status_code == 401
    assert Client().get("/monitor/feed/table/patients/").status_code == 401


def test_the_schema_lists_every_table_and_view_in_groups(root, visits):
    data = feed(root, "/monitor/feed/schema/").json()
    by_name = {r["name"]: r for r in data["relations"]}
    assert data["values_allowed"] is True
    for table in ("patients", "visits", "sys_budget_ledger", "sys_patient_ledger", "sys_principal", "django_session"):
        assert by_name[table]["kind"] == "table"
    for view in ("v_budget_status", "v_patient_budget", "v_query_audit", "v_visit_summary"):
        assert by_name[view]["kind"] == "view" and by_name[view]["group"] == "Views"
    assert by_name["patients"]["group"] == "Clinical data (SafeHealth)" and by_name["patients"]["rows"] == 6
    assert by_name["sys_role"]["group"] == "Governance and budgets (sys_*)"
    assert by_name["django_session"]["group"].startswith("Framework")


def test_a_plain_staff_sign_in_sees_structure_but_no_values(staff, visits):
    schema = feed(staff, "/monitor/feed/schema/").json()
    assert schema["values_allowed"] is False
    assert {r["name"]: r["rows"] for r in schema["relations"]}["sys_patient_ledger"] is None  # the count is data too
    table = feed(staff, "/monitor/feed/table/patients/").json()
    assert table["values_allowed"] is False and table["rows"] is None and table["total"] is None
    assert [c["name"] for c in table["columns"]][:2] == ["patient_id", "mrn"]  # the schema is still shown


def test_a_superuser_sees_columns_keys_constraints_indexes_and_rows(root, visits):
    d = feed(root, "/monitor/feed/table/visits/").json()
    cols = {c["name"]: c for c in d["columns"]}
    assert "PK" in cols["visit_id"]["keys"] and "FK" in cols["patient_id"]["keys"]
    assert cols["total_charge"]["nullable"] in (True, False) and "double" in cols["total_charge"]["type"]
    assert any(c["type"] == "f" and "patients" in c["definition"] for c in d["constraints"])
    assert any("patient" in i["definition"] for i in d["indexes"])
    assert d["total"] == 7 + 5 * 2 and len(d["rows"]) == 17 and len(d["rows"][0]) == len(d["columns"])
    ids = [row[0] for row in d["rows"]]
    assert ids == sorted(ids)  # ordered by the primary key


def test_triggers_and_view_definitions_are_shown(root, visits):
    ledger = feed(root, "/monitor/feed/table/sys_patient_ledger/").json()
    assert {"trg_patient_ledger_hash", "trg_patient_ledger_readonly"} <= set(ledger["triggers"])
    view = feed(root, "/monitor/feed/table/v_patient_budget/").json()
    assert view["kind"] == "view" and "sys_patient_budget" in view["definition"]
    assert view["total"] == 6 and view["rows"]


def test_secret_columns_are_never_read(root, visits, account, settings):
    """Passwords, live tokens, noise seeds and wrapped keys come back as '(hidden)' for every row."""
    from monitor.views import SECRET_COLUMNS, _relations
    client = APIClient()
    client.post("/api/auth/login/", {"username": "ops_root", "password": PASSWORD}, format="json")  # a live token
    hash_text = Principal.objects.get(username="ops_root").password
    seen = 0
    for name in _relations():
        d = feed(root, f"/monitor/feed/table/{name}/").json()
        for i, col in enumerate(d["columns"]):
            if col["name"] in SECRET_COLUMNS and d["rows"]:
                assert {row[i] for row in d["rows"]} == {"(hidden)"}, (name, col["name"])
                seen += 1
        assert hash_text not in str(d), name
    assert seen >= 3  # sys_principal.password, outstanding-token token/jti at least


def test_binary_columns_show_a_short_hex_prefix(root, account, visits, settings):
    from privacy.service import run_private_query
    from catalog.models import SysTable
    settings.PRIVGUARD_MIN_RESPONSE_MS = 0
    SysTable.objects.create(db=account.db, table_name="visits")
    run_private_query(account.principal, account.db.db_name, "SELECT COUNT(*) FROM visits", rho_req=0.05)
    d = feed(root, "/monitor/feed/table/sys_budget_ledger/").json()
    i = [c["name"] for c in d["columns"]].index("entry_hash")
    assert d["rows"] and all(r[i].startswith("\\x") and r[i].endswith("...") and len(r[i]) < 30 for r in d["rows"])


@pytest.mark.parametrize("bad", ["nonsense", "pg_class", "information_schema.tables", "patients;DROP TABLE patients",
                                 "patients%20--", "PATIENTS", "..%2fetc", 'patients"', "sys_principal--"])
def test_only_real_public_tables_and_views_can_be_opened(root, visits, bad):
    assert feed(root, f"/monitor/feed/table/{bad}/").status_code == 404
    assert Patient_count() == 6  # nothing was dropped


def Patient_count():
    from safehealth.models import Patient
    return Patient.objects.count()


def test_paging_is_clamped(root, visits):
    huge = feed(root, "/monitor/feed/table/visits/?limit=100000&offset=-5").json()
    assert huge["limit"] == 100 and huge["offset"] == 0 and len(huge["rows"]) == 17
    page = feed(root, "/monitor/feed/table/visits/?limit=5&offset=10").json()
    assert len(page["rows"]) == 5 and page["offset"] == 10 and page["rows"][0][0] == huge["rows"][10][0]
    junk = feed(root, "/monitor/feed/table/visits/?limit=abc&offset=xyz").json()
    assert junk["limit"] == 25 and junk["offset"] == 0
    assert feed(root, "/monitor/feed/table/visits/?offset=999999").json()["rows"] == []


def test_the_demo_session_sees_values_only_while_debug_is_on(db, settings, visits):
    settings.DEBUG, settings.PRIVGUARD_MONITOR_KEY = True, KEY
    client = Client()
    client.get(f"/monitor/enter/?key={KEY}")
    assert client.get("/monitor/feed/table/patients/").json()["values_allowed"] is True
    settings.DEBUG = False  # the session flag alone is not enough once demo mode is off
    assert client.get("/monitor/feed/table/patients/").json()["values_allowed"] is False


def test_the_browser_container_is_on_the_database_page_only(staff):
    client = staff_client(staff)
    assert 'id="br"' in client.get("/monitor/database/").content.decode()
    for other in ("backend", "login", "design"):
        assert 'id="br"' not in client.get(f"/monitor/{other}/").content.decode()


@pytest.mark.parametrize("name", PAGES)
def test_every_monitor_page_script_is_valid_javascript(staff, name, tmp_path):
    """A stray parenthesis in the page script leaves the page stuck on 'connecting' with no data,
    and no server-side test notices. Parse the real rendered script with Node when it is installed."""
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = staff_client(staff).get(f"/monitor/{name}/").content.decode()
    script = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(script) == 1
    path = tmp_path / f"{name}.js"
    path.write_text(script[0], encoding="utf-8")
    result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:400]
