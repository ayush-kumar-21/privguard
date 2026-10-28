"""The monitor: three live pages (backend, database, login flow) for watching the app work.

Access: a staff session (sign in at /admin/), or, in demo mode only (DEBUG on and
PRIVGUARD_MONITOR_KEY set), a one-time link from ./run.sh that opens a read-only monitor session so
the tabs open without a login on each. Nothing here changes any data.
"""
import hmac
import re
import time
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.db import connection
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from audit.models import SysQuery
from budget.models import BudgetAccount, BudgetLedger

from .middleware import LOG, STARTED

# Tables whose size depends on which patients a query matched. A monitor viewer who also runs
# queries could read the exact number of matching patients off the row count, so it is never shown.
HIDDEN_COUNTS = frozenset({"sys_patient_ledger"})

PAGES = {"backend": "Backend", "database": "Database", "design": "System design", "login": "Login flow"}
SESSION_FLAG = "monitor_pass"


def _allowed(request) -> bool:
    user = request.user
    return bool(request.session.get(SESSION_FLAG) or (user.is_authenticated and user.is_active and user.is_staff))


def monitor_only(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        if _allowed(request):
            return view(request, *args, **kwargs)
        if request.path.startswith("/monitor/feed/"):
            return JsonResponse({"error": "sign in at /admin/ first"}, status=401)
        return redirect_to_login(request.get_full_path(), "/admin/login/")
    return inner


def enter(request):
    """Demo-mode entry: a valid key opens a read-only monitor session."""
    key, expected = request.GET.get("key", ""), getattr(settings, "PRIVGUARD_MONITOR_KEY", "")
    if not (settings.DEBUG and expected and hmac.compare_digest(key.encode(), expected.encode())):
        raise Http404()
    request.session[SESSION_FLAG] = True
    target = request.GET.get("next", "/monitor/backend/")
    if not (target.startswith("/monitor/") and ".." not in target
            and url_has_allowed_host_and_scheme(target, allowed_hosts=None)):
        target = "/monitor/backend/"
    return HttpResponseRedirect(target)


@monitor_only
def page(request, name):
    if name not in PAGES:
        raise Http404()
    return render(request, "monitor/page.html", {"name": name, "pages": PAGES})


# ------------------------------------------------------------------ feeds
def _pct(sorted_values, q):
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))] if sorted_values else None


@monitor_only
def backend_feed(request):
    now, entries = time.time(), list(LOG)
    started = time.perf_counter()
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    db_ms = round((time.perf_counter() - started) * 1000.0, 2)
    ms = sorted(e["ms"] for e in entries)
    return JsonResponse({
        "uptime_s": int(now - STARTED),
        "db_ping_ms": db_ms,
        "requests_total": len(entries),
        "requests_last_minute": sum(1 for e in entries if now - e["t"] <= 60),
        "errors_5xx": sum(1 for e in entries if e["status"] >= 500),
        "errors_4xx": sum(1 for e in entries if 400 <= e["status"] < 500),
        "p50_ms": _pct(ms, 0.5),
        "p95_ms": _pct(ms, 0.95),
        "recent": entries[-60:][::-1],
    })


def _quote(name):
    return connection.ops.quote_name(name)


@monitor_only
def database_feed(request):
    with connection.cursor() as cur:
        cur.execute("SELECT version()")
        version = cur.fetchone()[0].split(" on ")[0]
        cur.execute(
            "SELECT d.datname, pg_database_size(d.datname), s.numbackends "
            "FROM pg_database d JOIN pg_stat_database s ON s.datid = d.oid "
            "WHERE NOT d.datistemplate ORDER BY d.datname"
        )
        databases = [{"name": n, "bytes": b, "connections": c} for n, b, c in cur.fetchall()]
        tables = []
        for table in sorted(connection.introspection.table_names()):
            if table in HIDDEN_COUNTS:
                tables.append({"name": table, "rows": None, "system": True})
                continue
            cur.execute(f"SELECT count(*) FROM {_quote(table)}")  # names come from the catalog
            tables.append({"name": table, "rows": cur.fetchone()[0], "system": table.startswith("sys_")})
    return JsonResponse({
        "engine": version,
        "current": connection.settings_dict["NAME"],
        "databases": databases,
        "tables": tables,
        "accounts": [
            {"user": a.principal.username, "rho_total": a.rho_total, "rho_remaining": a.rho_remaining,
             "max_queries": a.max_queries, "queries_used": a.queries_used}
            for a in BudgetAccount.objects.select_related("principal").order_by("account_id")
        ],
        "ledger": [
            {"id": e.entry_id, "user": e.account.principal.username, "op": e.op,
             "rho_delta": e.rho_delta, "rho_after": e.rho_after, "at": e.logged_at.isoformat()}
            for e in BudgetLedger.objects.select_related("account__principal").order_by("-entry_id")[:8]
        ],
        "queries": [
            {"id": q.query_id, "user": q.principal.username, "status": q.status,
             "rho": q.rho_charged, "sql": q.sql_text[:90], "at": q.submitted_at.isoformat()}
            for q in SysQuery.objects.select_related("principal").order_by("-query_id")[:8]
        ],
    })


@monitor_only
def login_feed(request):
    entries = list(LOG)
    auth = [e for e in entries if e["path"].startswith("/api/auth/")]

    def count(path, ok):
        return sum(1 for e in auth if e["path"] == path and e["method"] == "POST" and (e["status"] < 400) == ok)

    last_seen = {}
    for e in entries:
        if e["user"]:
            last_seen[e["user"]] = e["t"]
    Principal = get_user_model()
    return JsonResponse({
        "steps": {
            "login_ok": count("/api/auth/login/", True),
            "login_failed": count("/api/auth/login/", False),
            "refresh_ok": count("/api/auth/refresh/", True),
            "logout": count("/api/auth/logout/", True),
            "authenticated_requests": sum(1 for e in entries if e["user"] and not e["path"].startswith("/api/auth/login")),
        },
        "events": [e for e in auth][-40:][::-1],
        "users": [
            {"user": p.username, "active": p.is_active,
             "roles": sorted(r.role_name for r in p.roles.all()),
             "last_seen": last_seen.get(p.username)}
            for p in Principal.objects.prefetch_related("roles").order_by("id")
        ],
    })


# ------------------------------------------------------------------ system design: the plan
PHASE = re.compile(r"^##\s+(Phase\s+\d+)\s*[\u2014-]\s*(.+)$")
ITEM = re.compile(r"^- \[([ xX-])\]\s+(.*)$")


def parse_plan(text: str) -> dict:
    """Phases and their checkboxes from IMPLEMENTATION_PLAN.md, plus its "Resume point".

    `[x]` done, `[ ]` open, `[-]` dropped on purpose. Only `## Phase N` sections are read, so the
    bug log and notes do not count as work items."""
    phases, current, resume, in_resume = [], None, [], False
    for line in text.splitlines():
        if line.startswith("## "):
            in_resume = line.strip().lower() == "## resume point"
            m = PHASE.match(line.strip())
            current = {"id": m.group(1), "title": m.group(2).strip(), "items": []} if m else None
            if current:
                phases.append(current)
            continue
        if in_resume and line.startswith(">"):
            resume.append(line.lstrip("> ").replace("**", "").strip())
        m = ITEM.match(line.strip())
        if current is not None and m:
            current["items"].append({"state": {"x": "done", "X": "done", "-": "dropped"}.get(m.group(1), "open"),
                                     "text": m.group(2).replace("**", "").replace("`", "")[:160]})
    for p in phases:
        p["done"] = sum(1 for i in p["items"] if i["state"] == "done")
        p["total"] = sum(1 for i in p["items"] if i["state"] != "dropped")
    return {"phases": phases, "resume": [r for r in resume if r]}


@monitor_only
def design_feed(request):
    path = getattr(settings, "PRIVGUARD_PLAN_FILE", "")
    try:
        with open(path, encoding="utf-8") as f:
            plan = parse_plan(f.read())
        plan["available"] = True
    except OSError:
        plan = {"phases": [], "resume": [], "available": False}
    return JsonResponse(plan)


# ------------------------------------------------------------------ database browser
# Columns whose values are never read, whatever the viewer: credentials, live tokens, noise seeds
# and wrapped encryption keys. They are replaced inside the SQL, so they never reach this process.
SECRET_COLUMNS = frozenset({
    "password", "session_data", "session_key", "token", "jti", "noise_seed", "wrapped_dek",
})
CLINICAL_TABLES = frozenset({"regions", "hospitals", "departments", "staff", "patients",
                             "visits", "diagnoses", "lab_results"})
MAX_PAGE = 100


def _values_allowed(request) -> bool:
    """Raw rows show patient-level data and which patients each query touched, so only a superuser
    (who cannot run queries: separation of duty) or the demo-mode session may see them."""
    user = request.user
    if user.is_authenticated and user.is_active and user.is_superuser:
        return True
    return bool(settings.DEBUG and request.session.get(SESSION_FLAG))


def _group(name: str, kind: str) -> str:
    if kind == "view":
        return "Views"
    if name in CLINICAL_TABLES:
        return "Clinical data (SafeHealth)"
    if name.startswith("sys_"):
        return "Governance and budgets (sys_*)"
    return "Framework (auth, sessions, tokens)"


def _relations() -> dict:
    """{name: 'table' | 'view'} for the public schema, straight from the catalog."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') ORDER BY c.relname"
        )
        return {name: ("view" if kind == "v" else "table") for name, kind in cur.fetchall()}


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


@monitor_only
def schema_feed(request):
    allowed, relations, out = _values_allowed(request), _relations(), []
    with connection.cursor() as cur:
        for name, kind in relations.items():
            rows = None
            if allowed or name not in HIDDEN_COUNTS:
                cur.execute(f"SELECT count(*) FROM {_quote(name)}")  # names come from the catalog
                rows = cur.fetchone()[0]
            out.append({"name": name, "kind": kind, "group": _group(name, kind), "rows": rows})
    return JsonResponse({"relations": out, "values_allowed": allowed})


@monitor_only
def table_feed(request, name):
    relations = _relations()
    if name not in relations:  # an allow-list from the catalog, not a pattern
        raise Http404()
    kind, allowed = relations[name], _values_allowed(request)
    quoted = _quote(name)
    offset = _int(request.GET.get("offset"), 0, 0, 10**9)
    limit = _int(request.GET.get("limit"), 25, 1, MAX_PAGE)

    with connection.cursor() as cur:
        cur.execute(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull, "
            "       pg_get_expr(d.adbin, d.adrelid), a.atttypid = 'bytea'::regtype "
            "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
            [f"public.{_quote(name)}"],
        )
        cols = [{"name": n, "type": t, "nullable": nl, "default": df, "bytea": b} for n, t, nl, df, b in cur.fetchall()]

        cur.execute(
            "SELECT c.conname, c.contype, pg_get_constraintdef(c.oid), "
            "       ARRAY(SELECT attname FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey)) "
            "FROM pg_constraint c WHERE c.conrelid = %s::regclass ORDER BY c.contype, c.conname",
            [f"public.{_quote(name)}"],
        )
        constraints = [{"name": n, "type": t, "definition": d, "columns": list(cs)} for n, t, d, cs in cur.fetchall()]
        keys = {}
        for c in constraints:
            tag = {"p": "PK", "f": "FK", "u": "U"}.get(c["type"])
            if tag:
                for col in c["columns"]:
                    keys.setdefault(col, [])
                    if tag not in keys[col]:
                        keys[col].append(tag)
        for col in cols:
            col["keys"] = keys.get(col["name"], [])

        cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s ORDER BY indexname", [name])
        indexes = [{"name": n, "definition": d} for n, d in cur.fetchall()]
        cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal ORDER BY tgname", [f"public.{_quote(name)}"])
        triggers = [r[0] for r in cur.fetchall()]
        definition = None
        if kind == "view":
            cur.execute("SELECT pg_get_viewdef(%s::regclass, true)", [f"public.{_quote(name)}"])
            definition = cur.fetchone()[0]

        payload = {"name": name, "kind": kind, "group": _group(name, kind), "columns": cols,
                   "constraints": constraints, "indexes": indexes, "triggers": triggers,
                   "definition": definition, "values_allowed": allowed, "rows": None,
                   "total": None, "offset": offset, "limit": limit}
        if allowed:
            select = []
            for col in cols:
                q = _quote(col["name"])
                if col["name"] in SECRET_COLUMNS:
                    select.append(f"'(hidden)'::text AS {q}")
                elif col["bytea"]:
                    select.append(f"CASE WHEN {q} IS NULL THEN NULL ELSE '\\x' || encode(substring({q} from 1 for 8), 'hex') || '...' END AS {q}")
                else:
                    select.append(q)
            pk = next((c["columns"] for c in constraints if c["type"] == "p"), None)
            order = ", ".join(_quote(c) for c in pk) if pk else "1"
            cur.execute(f"SELECT count(*) FROM {quoted}")
            payload["total"] = cur.fetchone()[0]
            cur.execute(f"SELECT {', '.join(select)} FROM {quoted} ORDER BY {order} LIMIT %s OFFSET %s", [limit, offset])
            payload["rows"] = [[(v[:300] + "..." if isinstance(v, str) and len(v) > 300 else v) for v in row] for row in cur.fetchall()]
    return JsonResponse(payload)
