"""The connection guardrails must not drop options that DATABASE_URL carries (sslmode=require)."""
import environ

from privguard.dbopts import SESSION_OPTIONS, with_guardrails


def parsed(url):
    return environ.Env.db_url_config(url)


def test_ssl_mode_from_the_url_survives():
    db = with_guardrails(parsed("postgres://u:p@ep-x.neon.tech/db?sslmode=require"), 0)
    assert db["OPTIONS"]["sslmode"] == "require"           # the regression: this used to vanish
    assert db["OPTIONS"]["application_name"] == "privguard-api"
    assert "statement_timeout=15000" in db["OPTIONS"]["options"]
    assert db["CONN_MAX_AGE"] == 0 and db["CONN_HEALTH_CHECKS"] is True


def test_plain_urls_still_get_the_guardrails():
    db = with_guardrails(parsed("postgres://u:p@db:5432/d"), 60)
    assert db["OPTIONS"]["options"] == SESSION_OPTIONS and db["CONN_MAX_AGE"] == 60


def test_url_supplied_startup_options_are_extended_not_replaced():
    db = with_guardrails({"OPTIONS": {"options": "-c search_path=public"}}, 10)
    assert db["OPTIONS"]["options"].startswith("-c search_path=public ") and SESSION_OPTIONS in db["OPTIONS"]["options"]
