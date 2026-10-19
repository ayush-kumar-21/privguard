"""Shared helpers for the attack-evaluation experiments (run inside the backend env)."""
import hashlib
import math
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "privguard.settings")
for _k, _v in {
    "DATABASE_URL": "postgres://u:p@localhost:5432/x",
    "SECRET_KEY": "eval",
    "PRIVGUARD_SERVER_SECRET": "eval-noise-secret",
    "DEBUG": "False",
    "ALLOWED_HOSTS": "localhost,testserver",
    "CORS_ALLOWED_ORIGINS": "http://localhost:5173",
}.items():
    os.environ.setdefault(_k, _v)

import django  # noqa: E402

django.setup()

OUT = os.path.join(REPO, "docs", "eval")
os.makedirs(OUT, exist_ok=True)


def seed(*parts) -> bytes:
    """Stand-in for service._seed: a different key per (trial, principal)."""
    h = hashlib.sha256(b"eval-secret")
    for p in parts:
        h.update(str(p).encode() + b"|")
    return h.digest()


def wilson(k: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)
