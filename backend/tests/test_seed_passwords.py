"""Seeded accounts: the public demo password is for DEBUG only; production gets env-provided ones."""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import Principal
from safehealth.management.commands.seed_demo import DEMO_PASSWORD, PRINCIPALS, password_for

STRONG = "Zebra-Quartz-5821-xy"
ADMIN = "Tundra-Violet-9043-qz"


@pytest.fixture
def production(settings, monkeypatch):
    settings.DEBUG = False
    for var in ("PRIVGUARD_DEMO_PASSWORD", "PRIVGUARD_ADMIN_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_debug_uses_the_public_demo_password_unless_overridden(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.delenv("PRIVGUARD_DEMO_PASSWORD", raising=False)
    assert password_for("alice", "dp_analyst") == DEMO_PASSWORD
    monkeypatch.setenv("PRIVGUARD_DEMO_PASSWORD", STRONG)
    assert password_for("alice", "dp_analyst") == STRONG


@pytest.mark.parametrize("value", ["", "short", "privguard123", "x" * 11])
def test_production_refuses_missing_short_or_public_passwords(production, value):
    production.setenv("PRIVGUARD_DEMO_PASSWORD", value)
    production.setenv("PRIVGUARD_ADMIN_PASSWORD", ADMIN)
    with pytest.raises(CommandError, match="PRIVGUARD_DEMO_PASSWORD"):
        password_for("alice", "dp_analyst")


def test_production_gives_the_admin_its_own_password(production):
    production.setenv("PRIVGUARD_DEMO_PASSWORD", STRONG)
    production.setenv("PRIVGUARD_ADMIN_PASSWORD", ADMIN)
    assert password_for("alice", "dp_analyst") == STRONG and password_for("dave", "dba") == ADMIN
    production.delenv("PRIVGUARD_ADMIN_PASSWORD")
    with pytest.raises(CommandError, match="PRIVGUARD_ADMIN_PASSWORD"):
        password_for("dave", "dba")


@pytest.mark.django_db
def test_a_refused_seed_creates_no_account_at_all(production):
    """Regression: a refusal used to leave alice created without a password, never repaired later."""
    production.setenv("PRIVGUARD_DEMO_PASSWORD", STRONG)  # the admin password is missing
    with pytest.raises(CommandError):
        call_command("seed_demo", patients=5)
    assert Principal.objects.filter(username__in=PRINCIPALS).count() == 0


@pytest.mark.django_db
def test_a_production_seed_sets_only_the_env_passwords(production):
    production.setenv("PRIVGUARD_DEMO_PASSWORD", STRONG)
    production.setenv("PRIVGUARD_ADMIN_PASSWORD", ADMIN)
    call_command("seed_demo", patients=5)
    assert Principal.objects.get(username="alice").check_password(STRONG)
    assert Principal.objects.get(username="dave").check_password(ADMIN)
    for name in PRINCIPALS:
        assert not Principal.objects.get(username=name).check_password(DEMO_PASSWORD)


@pytest.mark.django_db
def test_an_account_left_without_a_password_is_repaired_on_the_next_seed(production):
    production.setenv("PRIVGUARD_DEMO_PASSWORD", STRONG)
    production.setenv("PRIVGUARD_ADMIN_PASSWORD", ADMIN)
    call_command("seed_demo", patients=5)
    alice = Principal.objects.get(username="alice")
    alice.set_unusable_password()
    alice.save()
    call_command("seed_demo", patients=5)
    assert Principal.objects.get(username="alice").check_password(STRONG)
