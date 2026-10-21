"""Gateway configuration, read from the environment.

The signing secret has no default. A built-in fallback would be a published secret: anyone who
read the source could mint a valid token. Startup refuses a missing or short secret instead,
mirroring what the legacy Django settings do for `SECRET_KEY`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MIN_SECRET_LENGTH = 32


class ConfigError(RuntimeError):
    """The gateway cannot start safely with this configuration."""


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    access_ttl_seconds: int = 15 * 60
    refresh_ttl_seconds: int = 7 * 24 * 3600
    #: Seed the demo principals (alice, frank, erin, carol, dave). Off by default.
    demo_users: bool = False
    demo_password: str = "privguard123"
    #: Origins allowed to call the API from a browser. Empty = same-origin only.
    cors_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.jwt_secret) < MIN_SECRET_LENGTH:
            raise ConfigError(
                f"PRIVGUARD_GATEWAY_SECRET must be at least {MIN_SECRET_LENGTH} characters"
            )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        secret = env.get("PRIVGUARD_GATEWAY_SECRET", "")
        if not secret:
            raise ConfigError("PRIVGUARD_GATEWAY_SECRET is not set")
        origins = tuple(o for o in env.get("PRIVGUARD_GATEWAY_CORS", "").split(",") if o)
        return cls(
            jwt_secret=secret,
            demo_users=env.get("PRIVGUARD_GATEWAY_DEMO_USERS", "") == "1",
            cors_origins=origins,
        )
