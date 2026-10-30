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
