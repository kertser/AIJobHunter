"""Password hashing, JWT token creation/validation, and helpers."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _ensure_secret(key: str) -> str:
    """Return *key* if truthy, otherwise generate a random 32-byte secret.

    The caller (app lifespan) should persist the generated key so it
    survives restarts.
    """
    return key if key else secrets.token_urlsafe(32)


def create_access_token(
    user_id: str,
    secret_key: str,
    *,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT access token."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": user_id,
        "exp": expire,
        "iat": now,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, secret_key: str) -> str | None:
    """Decode a JWT and return the ``sub`` (user_id) claim, or *None* on failure."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Standalone admin-password token (not tied to any user)
# ---------------------------------------------------------------------------

_ADMIN_TOKEN_SUBJECT = "__admin__"


def create_admin_token(
    secret_key: str,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT that proves the holder entered the admin password."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=1))
    payload: dict[str, Any] = {
        "sub": _ADMIN_TOKEN_SUBJECT,
        "scope": "admin",
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def verify_admin_token(token: str, secret_key: str) -> bool:
    """Return *True* if *token* is a valid, unexpired admin token."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return (
            payload.get("sub") == _ADMIN_TOKEN_SUBJECT
            and payload.get("scope") == "admin"
        )
    except JWTError:
        return False

