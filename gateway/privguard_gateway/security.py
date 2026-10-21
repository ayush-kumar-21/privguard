"""Password hashing (argon2id) and JWTs (python-jose, HS256)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from jose import JWTError, jwt

# Only HS256 is ever accepted when decoding. Passing the algorithm list explicitly is what
# stops a token with `alg: none`, or an RS256 header naming our secret as a public key.
_ALGORITHMS = ["HS256"]

_hasher = PasswordHasher()
#: Verified against when the username is unknown, so a miss costs as long as a wrong password.
DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


class TokenError(Exception):
    """The token is missing, malformed, expired, forged or of the wrong kind."""


@dataclass(frozen=True)
class Claims:
    subject: str
    kind: str
    jti: str
    expires_at: int


def new_jti() -> str:
    return uuid.uuid4().hex


def encode_token(
    secret: str, subject: str, kind: str, ttl_seconds: int, jti: str | None = None
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "type": kind,
            "jti": jti or new_jti(),
            "iat": now,
            "exp": now + ttl_seconds,
        },
        secret,
        algorithm="HS256",
    )


def decode_token(secret: str, token: str, expected_kind: str) -> Claims:
    try:
        payload = jwt.decode(token, secret, algorithms=_ALGORITHMS)
    except JWTError as e:
        raise TokenError(str(e)) from e
    if payload.get("type") != expected_kind:
        raise TokenError(f"expected a {expected_kind} token")
    sub, jti = payload.get("sub"), payload.get("jti")
    if not isinstance(sub, str) or not isinstance(jti, str):
        raise TokenError("token is missing its subject or id")
    return Claims(subject=sub, kind=expected_kind, jti=jti, expires_at=int(payload["exp"]))
