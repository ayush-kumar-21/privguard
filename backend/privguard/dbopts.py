"""Session guardrails for the database connection, merged with what DATABASE_URL already says."""

SESSION_OPTIONS = ("-c statement_timeout=15000 -c lock_timeout=3000 "
                   "-c idle_in_transaction_session_timeout=30000")


def with_guardrails(db: dict, conn_max_age: int) -> dict:
    """Add the pool/health settings and the session timeouts to a Django DATABASES entry.

    Options that came from the URL (for example `?sslmode=require`, which hosted Postgres such as
    Neon needs) are kept: overwriting OPTIONS would silently turn SSL off and the connection would
    be refused.
    """
    options = dict(db.get("OPTIONS") or {})
    options["application_name"] = "privguard-api"
    startup = options.get("options", "")
    options["options"] = (startup + " " + SESSION_OPTIONS).strip() if startup else SESSION_OPTIONS
    db.update({"CONN_MAX_AGE": conn_max_age, "CONN_HEALTH_CHECKS": True, "OPTIONS": options})
    return db


UNPOOLED_SUFFIXES = ("_URL_UNPOOLED", "_URL_NON_POOLING")


def database_url(environ, default: str) -> str:
    """The connection URL to use.

    A hosted-Postgres integration (Neon through Vercel) adds a pooled URL and an unpooled one under a
    prefix of your choice, e.g. STORAGE_URL and STORAGE_URL_UNPOOLED. The pooled address runs in
    transaction-pooling mode, which refuses the per-connection options this app sets
    (statement_timeout and friends), so the unpooled one is preferred when it exists. Otherwise
    DATABASE_URL is used as given; with neither, the default.
    """
    unpooled = sorted(k for k in environ if k.endswith(UNPOOLED_SUFFIXES) and environ[k])
    if unpooled:
        return environ[unpooled[0]]
    return environ.get("DATABASE_URL") or default
