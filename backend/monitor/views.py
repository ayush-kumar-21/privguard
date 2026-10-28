"""The monitor: three live pages (backend, database, login flow) for watching the app work.

Access: a staff session (sign in at /admin/), or, in demo mode only (DEBUG on and
PRIVGUARD_MONITOR_KEY set), a one-time link from ./run.sh that opens a read-only monitor session so
the tabs open without a login on each. Nothing here changes any data.
"""
import hmac
import importlib.metadata as metadata
import re
import time
from functools import lru_cache, wraps

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.db import connection
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import Resolver404, URLResolver, get_resolver, resolve
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


# ------------------------------------------------------------------ backend: what it is
LOCAL_APPS = ("accounts", "catalog", "budget", "audit", "safehealth", "api", "monitor")


def _clean_route(route: str) -> str:
    """'api/^regions/(?P<pk>[^/.]+)/$' -> '/api/regions/<pk>/' (DRF routers use regex routes)."""
    route = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"<\1>", route)
    return "/" + route.replace("^", "").replace("$", "").replace("\\", "")


def _walk(patterns, prefix=""):
    for p in patterns:
        route = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, route)
        else:
            yield route, p


def _first_line(obj, fallback: str) -> str:
    doc = (getattr(obj, "__doc__", "") or "").strip()
    return doc.splitlines()[0].strip() if doc else fallback


def _who(cls) -> str:
    """Plain-language answer to 'who may call this endpoint', from the view's permission classes."""
    if cls is None:
        return "anyone (no sign-in)"
    perms = getattr(cls, "permission_classes", None)
    if perms is None:
        return "default rules"
    if len(perms) == 0:
        return "anyone (no sign-in)"
    return "; ".join(_first_line(p, p.__name__) for p in perms)


@lru_cache(maxsize=1)
def _endpoints() -> tuple:
    out = []
    for route, pattern in _walk(get_resolver().url_patterns):
        if "format" in route or route.startswith(("admin/", "monitor/")):
            continue  # Django admin and this monitor are not the application's API
        cb = pattern.callback
        cls = getattr(cb, "cls", None) or getattr(cb, "view_class", None)
        actions = getattr(cb, "actions", None)
        if actions:
            methods = sorted(m.upper() for m in actions)
        elif cls is not None:
            methods = sorted(m.upper() for m in cls.http_method_names
                             if m not in ("options", "head", "trace") and hasattr(cls, m))
        else:
            methods = ["GET"]
        view = cls.__name__ if cls is not None else cb.__name__
        out.append({"route": _clean_route(route), "methods": methods, "view": view,
                    "who": _who(cls), "what": _first_line(cls, "") if cls is not None else ""})
    return tuple(out)


@lru_cache(maxsize=1)
def _apps() -> tuple:
    out = []
    for label in LOCAL_APPS:
        config = django_apps.get_app_config(label)
        models = [{"name": m.__name__, "table": m._meta.db_table, "fields": len(m._meta.concrete_fields),
                   "managed": m._meta.managed} for m in config.get_models()]
        out.append({"app": label, "models": models})
    return tuple(out)


@lru_cache(maxsize=1)
def _commands() -> tuple:
    from django.core.management import get_commands
    mine = sorted(name for name, app in get_commands().items() if app in LOCAL_APPS)
    return tuple({"name": name, "app": get_commands()[name]} for name in mine)


def _runtime() -> dict:
    """Versions and a short allow-list of settings. Never a dump of settings: no secrets, no keys."""
    jwt = getattr(settings, "SIMPLE_JWT", {})
    minutes = lambda key: round(jwt[key].total_seconds() / 60) if key in jwt else None
    packages = {}
    for name in ("Django", "djangorestframework", "djangorestframework-simplejwt", "psycopg",
                 "argon2-cffi", "sqlglot", "django-filter", "django-cors-headers"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    rest = getattr(settings, "REST_FRAMEWORK", {})
    return {
        "packages": packages,
        "settings": {
            "debug": settings.DEBUG,
            "password hasher": settings.PASSWORD_HASHERS[0].rsplit(".", 1)[-1],
            "access token (min)": minutes("ACCESS_TOKEN_LIFETIME"),
            "refresh token (min)": minutes("REFRESH_TOKEN_LIFETIME"),
            "list page size": rest.get("PAGE_SIZE"),
            "response time floor (ms)": getattr(settings, "PRIVGUARD_MIN_RESPONSE_MS", None),
            "allowed hosts": ", ".join(settings.ALLOWED_HOSTS),
            "cors origins": ", ".join(getattr(settings, "CORS_ALLOWED_ORIGINS", [])),
            "time zone": settings.TIME_ZONE,
        },
        "middleware": [m.rsplit(".", 1)[-1] for m in settings.MIDDLEWARE],
    }


def _db_logic() -> dict:
    """The logic that lives in PostgreSQL: procedures, functions (not extension-owned) and triggers."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT p.proname, CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END, "
            "       pg_get_function_identity_arguments(p.oid), p.prorettype = 'trigger'::regtype "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prokind IN ('p', 'f') "
            "  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') "
            "ORDER BY 2 DESC, 1"
        )
        routines = [{"name": n, "kind": ("trigger function" if trig else k), "args": a or ""} for n, k, a, trig in cur.fetchall()]
        cur.execute(
            "SELECT c.relname, t.tgname, p.proname, "
            "       CASE WHEN t.tgtype & 2 = 2 THEN 'BEFORE' WHEN t.tgtype & 64 = 64 THEN 'INSTEAD OF' ELSE 'AFTER' END "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal AND n.nspname = 'public' "
            "ORDER BY c.relname, t.tgname"
        )
        triggers = [{"table": t, "name": n, "function": f, "when": w} for t, n, f, w in cur.fetchall()]
    return {"routines": routines, "triggers": triggers}


def _traffic(entries: list) -> dict:
    """Per-route live numbers from the request log: {route: [calls, total_ms, n4xx, n5xx, last_status]}."""
    stats = {}
    for e in entries:
        try:
            route = _clean_route(resolve(e["path"]).route)
        except Resolver404:
            route = "(no such route)"
        s = stats.setdefault(route, [0, 0.0, 0, 0, None])
        s[0] += 1
        s[1] += e["ms"]
        s[2] += 1 if 400 <= e["status"] < 500 else 0
        s[3] += 1 if e["status"] >= 500 else 0
        s[4] = e["status"]
    return stats


@monitor_only
def backend_feed(request):
    now, entries = time.time(), list(LOG)
    started = time.perf_counter()
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    db_ms = round((time.perf_counter() - started) * 1000.0, 2)
    ms = sorted(e["ms"] for e in entries)
    traffic = _traffic(entries)
    endpoints = []
    for ep in _endpoints():
        calls, total, n4, n5, last = traffic.get(ep["route"], [0, 0.0, 0, 0, None])
        endpoints.append({**ep, "calls": calls, "avg_ms": round(total / calls, 1) if calls else None,
                          "errors_4xx": n4, "errors_5xx": n5, "last_status": last})
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
        "endpoints": endpoints,
        "unrouted": traffic.get("(no such route)", [0])[0],
        "apps": list(_apps()),
        "commands": list(_commands()),
        "db_logic": _db_logic(),
        "runtime": _runtime(),
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
