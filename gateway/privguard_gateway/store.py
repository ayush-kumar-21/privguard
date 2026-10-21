"""Principals and refresh-token bookkeeping.

In memory for now: Phase 3 moves principals into the engine's `sys_principal` table, which
already has the same three columns (id, name, argon2 hash).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from .security import hash_password

#: Roles that may see every principal's budget and audit rows.
PRIVILEGED_ROLES = frozenset({"dba", "auditor"})

# username -> (role, rho_total or None for "no budget account"). Same as the legacy seed_demo.
DEMO_PRINCIPALS: dict[str, tuple[str, float | None]] = {
    "alice": ("dp_analyst", 5.0),
    "frank": ("dp_analyst", 0.2),
    "erin": ("auditor", 5.0),
    "carol": ("clinician", None),
    "dave": ("dba", None),
}


@dataclass(frozen=True)
class Principal:
    id: int
    username: str
    roles: tuple[str, ...]
    password_hash: str
    is_active: bool = True
    date_joined: str = ""

    @property
    def is_privileged(self) -> bool:
        return bool(PRIVILEGED_ROLES.intersection(self.roles))

    @property
    def is_staff(self) -> bool:
        return "dba" in self.roles

    def public(self) -> dict:
        """The shape the frontend's `Principal` type expects (never includes the hash)."""
        return {
            "id": self.id,
            "username": self.username,
            "kind": "human",
            "is_active": self.is_active,
            "is_staff": self.is_staff,
            "roles": list(self.roles),
            "date_joined": self.date_joined,
        }


class UserStore:
    def __init__(self) -> None:
        self._by_name: dict[str, Principal] = {}
        self._by_id: dict[int, Principal] = {}

    def add(self, username: str, password: str, roles: tuple[str, ...]) -> Principal:
        if username in self._by_name:
            raise ValueError(f"duplicate username: {username}")
        p = Principal(
            id=len(self._by_id) + 1,
            username=username,
            roles=roles,
            password_hash=hash_password(password),
            date_joined=datetime.now(timezone.utc).isoformat(),
        )
        self._by_name[username] = p
        self._by_id[p.id] = p
        return p

    def by_username(self, username: str) -> Principal | None:
        return self._by_name.get(username)

    def by_id(self, principal_id: int) -> Principal | None:
        return self._by_id.get(principal_id)

    @classmethod
    def with_demo_principals(cls, password: str) -> "UserStore":
        store = cls()
        for name, (role, _) in DEMO_PRINCIPALS.items():
            store.add(name, password, (role,))
        return store


class RefreshRegistry:
    """Refresh tokens that may still be redeemed.

    Redeeming one removes it (rotation), so a stolen-and-replayed token fails; logout removes it
    without issuing a replacement.
    """

    def __init__(self) -> None:
        self._live: set[str] = set()
        self._lock = threading.Lock()

    def register(self, jti: str) -> None:
        with self._lock:
            self._live.add(jti)

    def redeem(self, jti: str) -> bool:
        with self._lock:
            if jti in self._live:
                self._live.remove(jti)
                return True
            return False
