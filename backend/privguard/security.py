"""Startup checks that refuse to run production with placeholder secrets."""
from django.core.exceptions import ImproperlyConfigured

# Values that appear in the repository (code defaults, .env.example, docker-compose).
# Anyone who can read the source knows them.
KNOWN_PLACEHOLDERS = frozenset({
    "",
    "dev-noise-secret",
    "change-me-too",
    "dev-insecure-change-me",
    "dev-insecure-change-me-please",
})


def check_secrets(debug: bool, **secrets: str) -> None:
    """Raise ImproperlyConfigured if a production (DEBUG off) secret is a known placeholder.

    * SECRET_KEY signs the login tokens: a known value lets anyone forge a JWT.
    * PRIVGUARD_SERVER_SECRET keys the noise: with it known, an analyst can recompute the
      noise for their own query (it is derived from secret + query hash + their id) and
      subtract it from the answer to read the exact count.
    """
    if debug:
        return
    bad = [name for name, value in secrets.items() if value in KNOWN_PLACEHOLDERS]
    if bad:
        raise ImproperlyConfigured(
            f"{', '.join(bad)} must be set to a long random value when DEBUG is off "
            "(it is unset or a placeholder that is public in the repository). "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
