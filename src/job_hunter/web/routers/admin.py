"""Admin router — user management and profile management for administrators."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db, require_admin_session

router = APIRouter(tags=["admin"])


class UserPatch(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


class AdminAuth(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Admin password gate  (not tied to any user account)
# ---------------------------------------------------------------------------

@router.post("/api/admin/auth")
async def admin_auth(body: AdminAuth, request: Request):
    """Verify the admin password and set an ``admin_token`` cookie.

    Returns 200 on success, 403 on bad password.  If no admin password
    is configured, always succeeds (for backward compatibility).
    The token is **not** tied to any user account.
    """
    settings = request.app.state.settings
    admin_pw = settings.admin_password

    if admin_pw and body.password != admin_pw:
        raise HTTPException(status_code=403, detail="Incorrect admin password")

    # Issue a short-lived admin session token (1 hour)
    from job_hunter.auth.security import create_admin_token

    secret_key: str = request.app.state.secret_key
    token = create_admin_token(secret_key, expires_delta=timedelta(hours=1))
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        "admin_token",
        token,
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    return response


@router.get("/api/admin/password-required")
async def admin_password_required(request: Request):
    """Check whether the admin panel requires a separate password."""
    settings = request.app.state.settings
    return {"password_required": bool(settings.admin_password)}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/admin")
async def admin_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "admin.html", {})


# ---------------------------------------------------------------------------
# API endpoints — user management (require admin session)
# ---------------------------------------------------------------------------

@router.get("/api/admin/users")
async def list_users(
    request: Request,
    session: Session = Depends(get_db),
    _admin=Depends(require_admin_session),
):
    from job_hunter.auth.repo import list_users as _list
    users = _list(session)
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "is_admin": u.is_admin,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login": u.last_login.isoformat() if u.last_login else None,
            }
            for u in users
        ],
    }


@router.patch("/api/admin/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatch,
    request: Request,
    session: Session = Depends(get_db),
    _admin=Depends(require_admin_session),
):
    from job_hunter.auth.repo import set_user_active, set_user_admin, get_user_by_id
    from job_hunter.web.deps import get_current_user_optional

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "Invalid user ID")

    target = get_user_by_id(session, uid)
    if target is None:
        raise HTTPException(404, "User not found")

    # Best-effort self-protection: if caller is identifiable, prevent self-harm
    caller = get_current_user_optional(request)
    if caller is not None and uid == caller.id:
        if body.is_active is False:
            raise HTTPException(400, "Cannot deactivate yourself")
        if body.is_admin is False:
            raise HTTPException(400, "Cannot remove your own admin role")

    if body.is_active is not None:
        set_user_active(session, uid, is_active=body.is_active)
    if body.is_admin is not None:
        set_user_admin(session, uid, is_admin=body.is_admin)

    return {"updated": True, "user_id": user_id}


@router.delete("/api/admin/users/{user_id}")
async def delete_user_endpoint(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    _admin=Depends(require_admin_session),
):
    from job_hunter.auth.repo import delete_user
    from job_hunter.web.deps import get_current_user_optional

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "Invalid user ID")

    # Best-effort self-protection
    caller = get_current_user_optional(request)
    if caller is not None and uid == caller.id:
        raise HTTPException(400, "Cannot delete yourself")

    ok = delete_user(session, uid)
    if not ok:
        raise HTTPException(404, "User not found")

    return {"deleted": True, "user_id": user_id}


# ---------------------------------------------------------------------------
# Profile management — admin can view/delete any user's profiles
# ---------------------------------------------------------------------------

@router.get("/api/admin/profiles")
async def list_all_profiles(
    request: Request,
    session: Session = Depends(get_db),
    _admin=Depends(require_admin_session),
):
    """List all users and their profile info (user_profile + search profiles)."""
    from job_hunter.auth.repo import list_users as _list
    from job_hunter.config.loader import load_user_profile, load_profiles

    settings = request.app.state.settings
    users = _list(session)
    result = []
    for u in users:
        user_dir = settings.data_dir / "users" / str(u.id)
        up_path = user_dir / "user_profile.yml"
        sp_path = user_dir / "profiles.yml"

        user_profile = None
        search_profiles: list = []
        if up_path.exists():
            try:
                up = load_user_profile(up_path)
                user_profile = up.model_dump()
            except Exception:
                user_profile = {"_error": "Failed to load"}
        if sp_path.exists():
            try:
                sps = load_profiles(sp_path)
                search_profiles = [sp.model_dump() for sp in sps]
            except Exception:
                search_profiles = [{"_error": "Failed to load"}]

        result.append({
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
            "has_user_profile": user_profile is not None,
            "user_profile_name": (user_profile or {}).get("name", ""),
            "user_profile_title": (user_profile or {}).get("title", ""),
            "search_profile_count": len(search_profiles),
            "search_profile_names": [
                sp.get("name", "") for sp in search_profiles if isinstance(sp, dict)
            ],
        })

    return {"profiles": result}


@router.delete("/api/admin/profiles/{user_id}")
async def delete_user_profiles(
    user_id: str,
    request: Request,
    _admin=Depends(require_admin_session),
):
    """Delete all profile files (user_profile.yml and profiles.yml) for a user."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "Invalid user ID")

    settings = request.app.state.settings
    user_dir = settings.data_dir / "users" / str(uid)

    deleted_files = []
    for fname in ("user_profile.yml", "profiles.yml"):
        fpath = user_dir / fname
        if fpath.exists():
            fpath.unlink()
            deleted_files.append(fname)

    return {
        "deleted": True,
        "user_id": user_id,
        "deleted_files": deleted_files,
    }


@router.delete("/api/admin/profiles/{user_id}/user-profile")
async def delete_user_profile_only(
    user_id: str,
    request: Request,
    _admin=Depends(require_admin_session),
):
    """Delete only the user_profile.yml for a specific user."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "Invalid user ID")

    settings = request.app.state.settings
    fpath = settings.data_dir / "users" / str(uid) / "user_profile.yml"
    if not fpath.exists():
        raise HTTPException(404, "User profile not found")
    fpath.unlink()
    return {"deleted": True, "user_id": user_id, "file": "user_profile.yml"}


@router.delete("/api/admin/profiles/{user_id}/search-profiles")
async def delete_search_profiles_only(
    user_id: str,
    request: Request,
    _admin=Depends(require_admin_session),
):
    """Delete only the profiles.yml (search profiles) for a specific user."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "Invalid user ID")

    settings = request.app.state.settings
    fpath = settings.data_dir / "users" / str(uid) / "profiles.yml"
    if not fpath.exists():
        raise HTTPException(404, "Search profiles not found")
    fpath.unlink()
    return {"deleted": True, "user_id": user_id, "file": "profiles.yml"}


# ---------------------------------------------------------------------------
# Database reset — nuclear option
# ---------------------------------------------------------------------------

@router.post("/api/admin/reset-db")
async def reset_database(
    request: Request,
    _admin=Depends(require_admin_session),
):
    """Drop ALL tables and re-create them from scratch.

    This deletes every job, score, application, user, market record, etc.
    After reset the caller is logged out (cookies cleared).
    """
    from job_hunter.db.models import Base
    import job_hunter.market.db_models  # noqa: F401
    import job_hunter.auth.models       # noqa: F401

    engine = request.app.state.engine
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from job_hunter.db.migrations import run_migrations
    run_migrations(engine)

    # Clear cookies — the user table no longer exists
    response = JSONResponse({"reset": True})
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("admin_token")
    return response

