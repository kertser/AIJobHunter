"""FastAPI dependency injection — DB session, settings, task manager, auth."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from job_hunter.config.models import AppSettings
from job_hunter.db.repo import make_session
from job_hunter.web.task_manager import TaskManager


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a DB session, commit on success, rollback on error."""
    session = make_session(request.app.state.engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.task_manager


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

def get_current_user(request: Request):
    """Return the authenticated User or raise 401.

    Reads the ``access_token`` cookie (or ``Authorization: Bearer`` header),
    decodes the JWT, and loads the User row from the DB.
    """
    from job_hunter.auth.security import decode_access_token
    from job_hunter.auth.repo import get_user_by_id

    secret_key: str = getattr(request.app.state, "secret_key", "")
    if not secret_key:
        raise HTTPException(500, "Server secret_key not configured")

    # Try cookie first, then Authorization header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = decode_access_token(token, secret_key)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    session = make_session(request.app.state.engine)
    try:
        user = get_user_by_id(session, user_id)
    finally:
        session.close()

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


def get_current_user_optional(request: Request):
    """Return the authenticated User or None (no exception)."""
    try:
        return get_current_user(request)
    except HTTPException:
        return None


def require_admin(request: Request):
    """Verify admin-password gate (no user role check).

    If ``admin_password`` is not configured, any logged-in user passes.
    Otherwise, the caller must have an ``admin_token`` cookie set via
    ``POST /api/admin/auth``.

    .. note:: This is intentionally **not** tied to any specific user.
    """
    settings: AppSettings = request.app.state.settings
    admin_pw = settings.admin_password
    if not admin_pw:
        # No admin password configured — anyone can access
        return None

    from job_hunter.auth.security import verify_admin_token

    secret_key: str = getattr(request.app.state, "secret_key", "")
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=403, detail="Admin password required")
    if not verify_admin_token(token, secret_key):
        raise HTTPException(status_code=403, detail="Admin session expired — re-enter password")
    return None


# Keep as an alias so existing ``Depends(require_admin_session)`` still works.
require_admin_session = require_admin


def get_user_data_dir(request: Request) -> Path:
    """Return the per-user data directory for the current user.

    Falls back to the global data_dir when no user is authenticated.
    """
    from job_hunter.auth.repo import get_user_data_dir as _udd

    settings = request.app.state.settings
    user = get_current_user_optional(request)
    if user is None:
        return settings.data_dir
    return _udd(settings.data_dir, user.id)


def get_effective_settings(request: Request) -> AppSettings:
    """Return an AppSettings copy with per-user overrides applied.

    The User row stores personal overrides (API keys, runtime flags, etc.).
    Only non-None user values win over the global defaults — NULL columns
    mean "inherit from global AppSettings".

    Sensitive fields are decrypted transparently using the app's secret key.
    """
    base = request.app.state.settings
    user = getattr(request.state, "user", None)
    if user is None:
        user = get_current_user_optional(request)
    if user is None:
        return base

    secret_key: str = getattr(request.app.state, "secret_key", "") or ""

    from job_hunter.auth.crypto import ENCRYPTED_FIELDS, decrypt_value

    # Collect only non-None overrides from the User row
    _OVERLAY_FIELDS = (
        "openai_api_key", "llm_provider", "local_llm_url", "local_llm_model",
        "llm_temperature", "llm_max_tokens",
        "email_provider",
        "resend_api_key", "smtp_host", "smtp_user", "smtp_password",
        "notification_email", "slowmo_ms", "smtp_port",
        "mock", "dry_run", "headless", "smtp_use_tls",
        "notifications_enabled",
    )
    overrides: dict = {}
    for field in _OVERLAY_FIELDS:
        val = getattr(user, field, None)
        if val is not None:
            # For strings, only override if non-empty
            if isinstance(val, str) and not val:
                continue
            # Decrypt sensitive fields
            if field in ENCRYPTED_FIELDS and isinstance(val, str) and secret_key:
                val = decrypt_value(val, secret_key)
            overrides[field] = val

    if not overrides:
        return base

    data = base.model_dump()
    data.update(overrides)
    return AppSettings(**data)


