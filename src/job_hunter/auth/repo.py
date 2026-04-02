"""User CRUD helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.auth.models import User
from job_hunter.auth.security import hash_password, verify_password


# ---------------------------------------------------------------------------
# Create / lookup
# ---------------------------------------------------------------------------

def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str = "",
    is_admin: bool = False,
) -> User:
    """Create and flush a new User.  Raises if email already taken."""
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password),
        display_name=display_name or email.split("@")[0],
        is_admin=is_admin,
    )
    session.add(user)
    session.flush()
    return user


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.execute(
        select(User).where(User.email == email.lower().strip())
    ).scalar_one_or_none()


def get_user_by_id(session: Session, user_id: uuid.UUID | str) -> User | None:
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    return session.execute(
        select(User).where(User.id == uid)
    ).scalar_one_or_none()



def authenticate_user(session: Session, email: str, password: str) -> User | None:
    """Return the user if credentials are valid, else None."""
    user = get_user_by_email(session, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login = datetime.now(timezone.utc)
    session.flush()
    return user


def list_users(session: Session) -> list[User]:
    return list(session.execute(select(User).order_by(User.created_at)).scalars().all())


def count_users(session: Session) -> int:
    from sqlalchemy import func
    return session.execute(select(func.count()).select_from(User)).scalar() or 0


def update_user_settings(
    session: Session,
    user_id: uuid.UUID,
    *,
    secret_key: str = "",
    **kwargs,
) -> User | None:
    """Update per-user settings fields on the User row.

    Only known settings columns are written; unknown keys are silently ignored.
    Sensitive fields (API keys, passwords) are encrypted at rest when
    *secret_key* is provided.

    Returns the updated User or None if not found.
    """
    _SETTINGS_FIELDS = {
        "openai_api_key", "llm_provider", "llm_temperature", "llm_max_tokens",
        "mock", "dry_run", "headless",
        "slowmo_ms", "email_provider", "resend_api_key", "smtp_host",
        "smtp_port", "smtp_user", "smtp_password", "smtp_use_tls",
        "notification_email", "notifications_enabled",
    }
    user = get_user_by_id(session, user_id)
    if user is None:
        return None

    from job_hunter.auth.crypto import ENCRYPTED_FIELDS, encrypt_value

    for key, value in kwargs.items():
        if key in _SETTINGS_FIELDS:
            if key in ENCRYPTED_FIELDS and secret_key and isinstance(value, str) and value:
                value = encrypt_value(value, secret_key)
            setattr(user, key, value)
    session.flush()
    return user


def set_user_active(session: Session, user_id: uuid.UUID, *, is_active: bool) -> User | None:
    """Activate or deactivate a user."""
    user = get_user_by_id(session, user_id)
    if user is None:
        return None
    user.is_active = is_active
    session.flush()
    return user


def set_user_admin(session: Session, user_id: uuid.UUID, *, is_admin: bool) -> User | None:
    """Promote or demote a user."""
    user = get_user_by_id(session, user_id)
    if user is None:
        return None
    user.is_admin = is_admin
    session.flush()
    return user


def delete_user(session: Session, user_id: uuid.UUID) -> bool:
    """Delete a user by ID. Returns True if found and deleted."""
    user = get_user_by_id(session, user_id)
    if user is None:
        return False
    session.delete(user)
    session.flush()
    return True


def update_user_profile(
    session: Session,
    user_id: uuid.UUID,
    *,
    display_name: str | None = None,
    email: str | None = None,
) -> User | None:
    """Update display_name and/or email. Returns updated User or None."""
    user = get_user_by_id(session, user_id)
    if user is None:
        return None
    if display_name is not None:
        user.display_name = display_name
    if email is not None:
        user.email = email.lower().strip()
    session.flush()
    return user


def change_user_password(
    session: Session,
    user_id: uuid.UUID,
    *,
    current_password: str,
    new_password: str,
) -> tuple[bool, str]:
    """Verify the current password and set a new one.

    Returns ``(True, "")`` on success or ``(False, reason)`` on failure.
    """
    user = get_user_by_id(session, user_id)
    if user is None:
        return False, "User not found"
    if not verify_password(current_password, user.password_hash):
        return False, "Current password is incorrect"
    if len(new_password) < 8:
        return False, "New password must be at least 8 characters"
    user.password_hash = hash_password(new_password)
    session.flush()
    return True, ""


# ---------------------------------------------------------------------------
# Per-user data directory
# ---------------------------------------------------------------------------

def get_user_data_dir(base_data_dir: Path, user_id: uuid.UUID | str) -> Path:
    """Return (and create) the per-user data directory: data/users/<user_id>/"""
    p = base_data_dir / "users" / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p



