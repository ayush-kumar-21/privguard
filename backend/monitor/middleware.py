"""Remember the last few hundred API requests so the monitor can show the backend's flow.

The log lives in this process's memory (a bounded deque): nothing is written to disk, and a restart
starts it empty. With several worker processes each one keeps its own log, which is fine for a
demo server and the reason this is a monitor, not an audit trail (the audit trail is `sys_query`
and the budget ledger).

What is recorded: time, method, path (no query string, which can carry tokens), status, duration
and the signed-in username. For a login attempt only the *username* is read from the body; the
password is never looked at.
"""
import json
import re
import time
from collections import deque

LOG: deque = deque(maxlen=400)
STARTED = time.time()

_SKIP = ("/monitor/", "/static/", "/favicon")
_NAME = re.compile(r"[A-Za-z0-9_.@-]{1,64}")


def _login_name(request):
    """Username from a login POST, or None. Bounded, and never touches the password."""
    if request.path != "/api/auth/login/" or request.method != "POST":
        return None
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 4096:
            return None
        name = json.loads(request.body or b"{}").get("username")
    except (ValueError, AttributeError, TypeError):
        return None
    return name if isinstance(name, str) and _NAME.fullmatch(name) else "?"


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(_SKIP):
            return self.get_response(request)
        started = time.perf_counter()
        attempted = _login_name(request)
        response = self.get_response(request)
        user = getattr(request, "user", None)  # DRF's JWT auth sets this on the Django request too
        name = user.username if user is not None and user.is_authenticated else attempted
        LOG.append({
            "t": time.time(),
            "method": request.method,
            "path": request.path[:200],
            "status": response.status_code,
            "ms": round((time.perf_counter() - started) * 1000.0, 1),
            "user": name,
        })
        return response
