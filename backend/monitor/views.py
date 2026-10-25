"""The monitor: three live pages (backend, database, login flow) for watching the app work.

Access: a staff session (sign in at /admin/), or, in demo mode only (DEBUG on and
PRIVGUARD_MONITOR_KEY set), a one-time link from ./run.sh that opens a read-only monitor session so
the tabs open without a login on each. Nothing here changes any data.
"""
import hmac
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
from budget.models import BudgetAccount, BudgetLedger, PatientBudgetView

from .middleware import LOG, STARTED

PAGES = {"backend": "Backend", "database": "Database", "login": "Login flow"}
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
        "patients": [
            {"id": b.patient_id, "rho_total": b.rho_total, "rho_spent": b.rho_spent,
             "status": b.status, "charges": b.charges}
            for b in PatientBudgetView.objects.order_by("-rho_spent", "patient_id")[:8]
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
