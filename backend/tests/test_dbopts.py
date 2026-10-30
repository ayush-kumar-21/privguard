"""The connection guardrails must not drop options that DATABASE_URL carries (sslmode=require)."""
import environ

from privguard.dbopts import SESSION_OPTIONS, database_url, with_guardrails


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


DEFAULT = "postgres://d:d@localhost/d"


def test_the_unpooled_url_from_a_hosted_integration_is_preferred():
    env = {"STORAGE_URL": "postgres://pooled-pooler/x", "STORAGE_URL_UNPOOLED": "postgres://direct/x",
           "DATABASE_URL": "postgres://explicit/x"}
    assert database_url(env, DEFAULT) == "postgres://direct/x"


def test_any_prefix_and_the_non_pooling_spelling_work():
    assert database_url({"NEON_URL_UNPOOLED": "postgres://a/x"}, DEFAULT) == "postgres://a/x"
    assert database_url({"POSTGRES_URL_NON_POOLING": "postgres://b/x"}, DEFAULT) == "postgres://b/x"


def test_without_one_database_url_then_the_default_are_used():
    assert database_url({"DATABASE_URL": "postgres://explicit/x"}, DEFAULT) == "postgres://explicit/x"
    assert database_url({}, DEFAULT) == DEFAULT
    assert database_url({"STORAGE_URL_UNPOOLED": ""}, DEFAULT) == DEFAULT  # an empty value is ignored


def test_the_pooled_url_alone_is_never_mistaken_for_an_unpooled_one():
    assert database_url({"STORAGE_URL": "postgres://pooled-pooler/x"}, DEFAULT) == DEFAULT
