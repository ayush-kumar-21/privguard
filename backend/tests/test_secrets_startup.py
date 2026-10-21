"""
P0-3 regression tests: the stack must not start on public placeholder secrets, and
the compose file must not carry any.

The first group imports the *real* settings module in a subprocess (settings read the
environment at import, and a failed import must not poison this test process).
The second group guards the committed files: docker-compose.yml, .env.example, .gitignore.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
GOOD = "k" * 48


def _import_settings(**overrides):
    env = {k: v for k, v in os.environ.items()
           if k not in {"DEBUG", "SECRET_KEY", "PRIVGUARD_SERVER_SECRET"}}
    env.update({
        "DJANGO_SETTINGS_MODULE": "privguard.settings",
        "DATABASE_URL": "postgres://u:p@localhost:5432/d",
    })
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import privguard.settings"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60,
    )


# ------------------------------------------------------------ refuses to start
@pytest.mark.parametrize("placeholder", [
    "dev-insecure-change-me", "dev-insecure-change-me-please", "__GENERATE__", "",
])
def test_default_secret_key_refuses_to_start_when_debug_is_off(placeholder):
    r = _import_settings(DEBUG="False", SECRET_KEY=placeholder, PRIVGUARD_SERVER_SECRET=GOOD)
    assert r.returncode != 0
    assert "ImproperlyConfigured" in r.stderr and "SECRET_KEY" in r.stderr


def test_default_server_secret_refuses_to_start_when_debug_is_off():
    r = _import_settings(DEBUG="False", SECRET_KEY=GOOD)  # server secret left to its default
    assert r.returncode != 0
    assert "PRIVGUARD_SERVER_SECRET" in r.stderr


def test_real_secrets_start_when_debug_is_off():
    r = _import_settings(DEBUG="False", SECRET_KEY=GOOD, PRIVGUARD_SERVER_SECRET=GOOD + "x")
    assert r.returncode == 0, r.stderr


def test_placeholders_are_tolerated_only_in_debug():
    r = _import_settings(DEBUG="True", SECRET_KEY="dev-insecure-change-me")
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------- committed artefacts
def _compose_text():
    return (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_compose_has_no_committed_secret_or_debug_literal():
    text = _compose_text()
    from privguard.security import KNOWN_PLACEHOLDERS
    for bad in sorted(p for p in KNOWN_PLACEHOLDERS if p):
        assert bad not in text, f"placeholder {bad!r} is hardcoded in docker-compose.yml"
    assert not re.search(r"^\s*DEBUG:\s*[\"']?true[\"']?\s*$", text, re.I | re.M)
    assert not re.search(r"POSTGRES_PASSWORD:\s*privguard\b", text)
    assert "privguard:privguard@" not in text


def test_compose_requires_every_secret_from_the_environment():
    text = _compose_text()
    for name in ("SECRET_KEY", "PRIVGUARD_SERVER_SECRET", "POSTGRES_PASSWORD"):
        assert "${%s:?" % name in text, f"{name} must be a required compose variable"


def test_env_example_holds_only_markers_and_env_is_git_ignored():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1) for line in example.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    )
    for name in ("SECRET_KEY", "PRIVGUARD_SERVER_SECRET", "POSTGRES_PASSWORD"):
        assert values[name].strip() == "__GENERATE__"
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignored]
