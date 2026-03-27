"""Account router — view/edit personal account settings (email, display name, password)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db

router = APIRouter(tags=["account"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProfileUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@router.get("/account")
async def account_page(request: Request):
    """Render the account settings page."""
    user = request.state.user  # set by login_required_middleware
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "account.html", {"user": user})


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.put("/api/account/profile")
async def update_profile(
    body: ProfileUpdate,
    request: Request,
    session: Session = Depends(get_db),
):
    """Update display name and/or email."""
    from job_hunter.auth.repo import update_user_profile, get_user_by_email

    user = request.state.user

    # Validate email uniqueness if changing
    if body.email and body.email.lower().strip() != user.email:
        existing = get_user_by_email(session, body.email)
        if existing:
            return JSONResponse({"error": "Email already in use"}, status_code=409)

    updated = update_user_profile(
        session,
        user.id,
        display_name=body.display_name,
        email=body.email,
    )
    if updated is None:
        return JSONResponse({"error": "User not found"}, status_code=404)

    return {
        "updated": True,
        "display_name": updated.display_name,
        "email": updated.email,
    }


@router.put("/api/account/password")
async def change_password(
    body: PasswordChange,
    request: Request,
    session: Session = Depends(get_db),
):
    """Change the current user's password."""
    from job_hunter.auth.repo import change_user_password

    user = request.state.user

    if body.new_password != body.confirm_password:
        return JSONResponse({"error": "New passwords do not match"}, status_code=400)

    ok, reason = change_user_password(
        session,
        user.id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    if not ok:
        return JSONResponse({"error": reason}, status_code=400)

    return {"updated": True}


