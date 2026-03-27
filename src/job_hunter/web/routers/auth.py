"""Auth router — login, register, logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    admin_password: str = ""  # only meaningful for the first user


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/login")
async def login_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "login.html", {})


@router.get("/register")
async def register_page(request: Request):
    settings = request.app.state.settings
    if not settings.registration_enabled:
        return RedirectResponse(url="/login", status_code=302)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "register.html", {})


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/api/auth/register")
async def api_register(body: RegisterRequest, request: Request, session: Session = Depends(get_db)):
    settings = request.app.state.settings
    if not settings.registration_enabled:
        return JSONResponse({"error": "Registration is disabled"}, status_code=403)

    from job_hunter.auth.repo import create_user, get_user_by_email, count_users

    if get_user_by_email(session, body.email):
        return JSONResponse({"error": "Email already registered"}, status_code=409)

    if len(body.password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)

    # First user is always admin
    is_first = count_users(session) == 0
    user = create_user(
        session,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        is_admin=is_first,
    )
    session.commit()

    # First user may set the admin panel password
    if is_first and body.admin_password:
        settings.admin_password = body.admin_password
        try:
            from job_hunter.config.loader import save_settings_env
            dotenv_path = getattr(request.app.state, "dotenv_path", None)
            save_settings_env(settings, dotenv_path)
        except Exception:
            pass  # best-effort persistence

    # Auto-login
    from job_hunter.auth.security import create_access_token
    secret_key = request.app.state.secret_key
    token = create_access_token(str(user.id), secret_key)

    response = JSONResponse({"registered": True, "user_id": str(user.id)})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
        path="/",
    )
    return response


@router.get("/api/auth/is-first-user")
async def is_first_user(request: Request, session: Session = Depends(get_db)):
    """Return whether there are zero registered users (for the register page)."""
    from job_hunter.auth.repo import count_users
    return {"is_first_user": count_users(session) == 0}


@router.post("/api/auth/login")
async def api_login(body: LoginRequest, request: Request, session: Session = Depends(get_db)):
    from job_hunter.auth.repo import authenticate_user
    from job_hunter.auth.security import create_access_token

    user = authenticate_user(session, body.email, body.password)
    if user is None:
        return JSONResponse({"error": "Invalid email or password"}, status_code=401)

    session.commit()

    secret_key = request.app.state.secret_key
    token = create_access_token(str(user.id), secret_key)

    response = JSONResponse({
        "logged_in": True,
        "user_id": str(user.id),
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    })
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return response


@router.post("/api/auth/logout")
async def api_logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token", path="/")
    return response


@router.get("/api/auth/me")
async def api_me(request: Request):
    from job_hunter.web.deps import get_current_user_optional
    user = get_current_user_optional(request)
    if user is None:
        return JSONResponse({"authenticated": False})
    return {
        "authenticated": True,
        "user_id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }

