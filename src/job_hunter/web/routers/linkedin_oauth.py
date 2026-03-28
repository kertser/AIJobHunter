"""LinkedIn OAuth 2.0 Backend-for-Frontend (BFF) router.

Implements the Authorization Code flow where the *server* handles all
token exchange.  LinkedIn tokens are stored on the ``User`` row and
**never** exposed to the browser.

Endpoints:
    GET  /auth/linkedin            — redirect to LinkedIn authorization page
    GET  /auth/linkedin/callback   — exchange code for token, login/register
    POST /api/settings/linkedin-disconnect — clear stored token
    GET  /api/settings/linkedin-status     — check connection status

LinkedIn OIDC scopes: ``openid profile email``
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db

logger = logging.getLogger("job_hunter.web.linkedin_oauth")

router = APIRouter(tags=["linkedin-oauth"])

# LinkedIn OAuth endpoints
_AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

# OIDC scopes — works with all new LinkedIn apps
_SCOPES = "openid profile email"

# Cookie name for CSRF state
_STATE_COOKIE = "linkedin_oauth_state"


def _get_oauth_config(request: Request) -> tuple[str, str, str]:
    """Return (client_id, client_secret, redirect_uri) from settings."""
    settings = request.app.state.settings
    return (
        settings.linkedin_client_id,
        settings.linkedin_client_secret,
        settings.linkedin_redirect_uri,
    )


def _oauth_configured(request: Request) -> bool:
    """Return True if LinkedIn OAuth credentials are present."""
    cid, secret, redirect = _get_oauth_config(request)
    return bool(cid and secret and redirect)


# ---------------------------------------------------------------------------
# 1. Initiate — redirect user to LinkedIn
# ---------------------------------------------------------------------------

@router.get("/auth/linkedin")
async def linkedin_authorize(request: Request):
    """Redirect the user to LinkedIn's authorization page.

    Generates a random ``state`` value, stores it in a short-lived cookie,
    and redirects to LinkedIn with the required query parameters.
    """
    client_id, _secret, redirect_uri = _get_oauth_config(request)
    if not client_id or not redirect_uri:
        return JSONResponse(
            {"error": "LinkedIn OAuth is not configured. Set JOBHUNTER_LINKEDIN_CLIENT_ID, "
                      "JOBHUNTER_LINKEDIN_CLIENT_SECRET, and JOBHUNTER_LINKEDIN_REDIRECT_URI."},
            status_code=400,
        )

    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": _SCOPES,
    }
    url = str(httpx.URL(_AUTHORIZE_URL, params=params))

    response = RedirectResponse(url=url, status_code=302)
    # Store state in a short-lived cookie for CSRF validation
    response.set_cookie(
        key=_STATE_COOKIE,
        value=state,
        httponly=True,
        samesite="lax",
        max_age=600,  # 10 minutes
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# 2. Callback — exchange code for token, fetch profile, login/register
# ---------------------------------------------------------------------------

@router.get("/auth/linkedin/callback")
async def linkedin_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    session: Session = Depends(get_db),
):
    """Handle the OAuth callback from LinkedIn.

    1. Validate the ``state`` parameter against the cookie.
    2. Exchange the authorization ``code`` for an access token.
    3. Fetch the user's profile from LinkedIn's userinfo endpoint.
    4. Link to an existing user (by LinkedIn member ID or email), or
       create a new user.
    5. Issue a JWT session cookie and redirect to the app.
    """
    # Clean up the state cookie regardless of outcome
    if error:
        logger.warning("LinkedIn OAuth error: %s — %s", error, error_description)
        return RedirectResponse(url=f"/login?error={error_description or error}", status_code=302)

    if not code:
        return RedirectResponse(url="/login?error=Missing+authorization+code", status_code=302)

    # CSRF check
    expected_state = request.cookies.get(_STATE_COOKIE, "")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        logger.warning("LinkedIn OAuth state mismatch (possible CSRF)")
        return RedirectResponse(url="/login?error=Invalid+state+parameter", status_code=302)

    client_id, client_secret, redirect_uri = _get_oauth_config(request)

    # ── Exchange code for access token ──
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            token_resp = await http.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        logger.error("LinkedIn token exchange HTTP error: %s", exc)
        return RedirectResponse(url="/login?error=Token+exchange+failed", status_code=302)

    if token_resp.status_code != 200:
        logger.error("LinkedIn token exchange failed: %s %s", token_resp.status_code, token_resp.text)
        return RedirectResponse(url="/login?error=Token+exchange+failed", status_code=302)

    token_data = token_resp.json()
    access_token = token_data.get("access_token", "")
    expires_in = token_data.get("expires_in", 0)  # seconds

    if not access_token:
        return RedirectResponse(url="/login?error=No+access+token+received", status_code=302)

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None

    # ── Fetch user profile from LinkedIn ──
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            profile_resp = await http.get(
                _USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        logger.error("LinkedIn userinfo HTTP error: %s", exc)
        return RedirectResponse(url="/login?error=Failed+to+fetch+profile", status_code=302)

    if profile_resp.status_code != 200:
        logger.error("LinkedIn userinfo failed: %s %s", profile_resp.status_code, profile_resp.text)
        return RedirectResponse(url="/login?error=Failed+to+fetch+profile", status_code=302)

    profile = profile_resp.json()
    linkedin_sub = profile.get("sub", "")  # LinkedIn member ID
    li_email = (profile.get("email") or "").lower().strip()
    li_name = profile.get("name") or ""

    if not linkedin_sub:
        return RedirectResponse(url="/login?error=LinkedIn+did+not+return+member+ID", status_code=302)

    # ── Find or create user ──
    from job_hunter.auth.repo import (
        count_users,
        create_user,
        get_user_by_email,
        get_user_by_linkedin_id,
        update_linkedin_token,
        update_user_profile,
    )

    # 1. Try lookup by LinkedIn member ID (returning user)
    user = get_user_by_linkedin_id(session, linkedin_sub)

    # 2. Try lookup by email match
    if user is None and li_email:
        user = get_user_by_email(session, li_email)
        if user is not None:
            # Link the existing account to this LinkedIn identity
            user.linkedin_member_id = linkedin_sub
            session.flush()

    # 3. Create a new user (auto-register via LinkedIn)
    if user is None:
        is_first = count_users(session) == 0
        # Generate a random password; user can set one later via Account page
        random_password = secrets.token_urlsafe(24)
        user = create_user(
            session,
            email=li_email or f"linkedin-{linkedin_sub}@placeholder.local",
            password=random_password,
            display_name=li_name,
            is_admin=is_first,
        )
        user.linkedin_member_id = linkedin_sub
        session.flush()
        logger.info("Created new user via LinkedIn OAuth: %s (%s)", li_email, linkedin_sub)

    # ── Store the LinkedIn access token (BFF — server-side only) ──
    update_linkedin_token(
        session,
        user.id,
        access_token=access_token,
        expires_at=expires_at,
        member_id=linkedin_sub,
    )

    # Update display name if empty
    if not user.display_name and li_name:
        update_user_profile(session, user.id, display_name=li_name)

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    session.flush()
    session.commit()

    # ── Issue JWT session cookie ──
    from job_hunter.auth.security import create_access_token

    secret_key = request.app.state.secret_key
    token = create_access_token(str(user.id), secret_key)

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
        path="/",
    )
    # Clean up state cookie
    response.delete_cookie(_STATE_COOKIE, path="/")
    return response


# ---------------------------------------------------------------------------
# 3. Disconnect — clear stored token
# ---------------------------------------------------------------------------

@router.post("/api/settings/linkedin-disconnect")
async def linkedin_disconnect(request: Request, session: Session = Depends(get_db)):
    """Remove the stored LinkedIn OAuth token for the current user."""
    from job_hunter.auth.repo import clear_linkedin_token

    user = getattr(request.state, "user", None)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    clear_linkedin_token(session, user.id)
    session.commit()
    return {"disconnected": True}


# ---------------------------------------------------------------------------
# 4. Status — check connection status
# ---------------------------------------------------------------------------

@router.get("/api/settings/linkedin-status")
async def linkedin_status(request: Request):
    """Return the LinkedIn OAuth connection status for the current user.

    When called without authentication (e.g. from the login page) returns
    only ``oauth_configured`` so the frontend knows whether to show the
    "Sign in with LinkedIn" button.
    """
    configured = _oauth_configured(request)

    # Unauthenticated callers only need to know if OAuth is configured
    user = getattr(request.state, "user", None)
    if user is None:
        from job_hunter.web.deps import get_current_user_optional
        user = get_current_user_optional(request)

    if user is None:
        return {
            "connected": False,
            "member_id": "",
            "display_name": "",
            "expired": False,
            "oauth_configured": configured,
        }

    connected = bool(user.linkedin_access_token and user.linkedin_member_id)
    expired = False
    if connected and user.linkedin_token_expires_at:
        # SQLite may return naive datetimes — treat them as UTC
        exp = user.linkedin_token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        expired = exp < datetime.now(timezone.utc)

    return {
        "connected": connected and not expired,
        "member_id": user.linkedin_member_id or "",
        "display_name": user.display_name or "",
        "expired": expired,
        "oauth_configured": configured,
    }

