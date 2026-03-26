"""Settings router — view/edit runtime configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db

router = APIRouter(tags=["settings"])


class SettingsUpdate(BaseModel):
    mock: bool | None = None
    dry_run: bool | None = None
    headless: bool | None = None
    slowmo_ms: int | None = None
    log_level: str | None = None
    openai_api_key: str | None = None
    # Email notification settings
    email_provider: str | None = None
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool | None = None
    notification_email: str | None = None
    notifications_enabled: bool | None = None


@router.get("/settings")
async def settings_page(request: Request):
    templates = request.app.state.templates
    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    return templates.TemplateResponse(request, "settings.html", {
        "settings": settings,
        "api_key_masked": _mask_key(settings.openai_api_key),
        "smtp_password_masked": _mask_key(settings.smtp_password),
        "resend_api_key_masked": _mask_key(settings.resend_api_key),
    })


@router.get("/api/settings")
async def get_settings_api(request: Request):
    from job_hunter.web.deps import get_effective_settings
    s = get_effective_settings(request)
    return {
        "mock": s.mock,
        "dry_run": s.dry_run,
        "headless": s.headless,
        "slowmo_ms": s.slowmo_ms,
        "log_level": s.log_level.value,
        "data_dir": str(s.data_dir),
        "llm_provider": s.llm_provider,
        "openai_api_key": _mask_key(s.openai_api_key),
        "email_provider": s.email_provider,
        "resend_api_key": _mask_key(s.resend_api_key),
        "smtp_host": s.smtp_host,
        "smtp_port": s.smtp_port,
        "smtp_user": s.smtp_user,
        "notification_email": s.notification_email,
        "notifications_enabled": s.notifications_enabled,
    }


@router.put("/api/settings")
async def update_settings_api(body: SettingsUpdate, request: Request, session: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)

    # Collect non-None updates
    updates: dict = {}
    if body.mock is not None:
        updates["mock"] = body.mock
    if body.dry_run is not None:
        updates["dry_run"] = body.dry_run
    if body.headless is not None:
        updates["headless"] = body.headless
    if body.slowmo_ms is not None:
        updates["slowmo_ms"] = body.slowmo_ms
    if body.log_level is not None:
        from job_hunter.config.models import LogLevel
        updates["log_level"] = body.log_level  # store as string for User row
    if body.openai_api_key and body.openai_api_key.strip():
        updates["openai_api_key"] = body.openai_api_key.strip()
    if body.email_provider is not None:
        updates["email_provider"] = body.email_provider
    if body.resend_api_key and body.resend_api_key.strip():
        updates["resend_api_key"] = body.resend_api_key.strip()
    if body.smtp_host is not None:
        updates["smtp_host"] = body.smtp_host
    if body.smtp_port is not None:
        updates["smtp_port"] = body.smtp_port
    if body.smtp_user is not None:
        updates["smtp_user"] = body.smtp_user
    if body.smtp_password and body.smtp_password.strip():
        updates["smtp_password"] = body.smtp_password.strip()
    if body.smtp_use_tls is not None:
        updates["smtp_use_tls"] = body.smtp_use_tls
    if body.notification_email is not None:
        updates["notification_email"] = body.notification_email
    if body.notifications_enabled is not None:
        updates["notifications_enabled"] = body.notifications_enabled

    if user is not None:
        # Write per-user settings to the User row
        from job_hunter.auth.repo import update_user_settings
        update_user_settings(session, user.id, **updates)
    else:
        # Fallback: mutate global settings (legacy / CLI)
        s = request.app.state.settings
        for k, v in updates.items():
            if k == "log_level":
                from job_hunter.config.models import LogLevel
                s.log_level = LogLevel(v)
            elif hasattr(s, k):
                setattr(s, k, v)
        from job_hunter.config.loader import save_settings_env
        dotenv_path = getattr(request.app.state, "dotenv_path", None)
        save_settings_env(s, dotenv_path)

    return {"updated": True}


@router.post("/api/settings/test-email")
async def test_email(request: Request):
    """Send a test email to verify notification configuration."""
    from job_hunter.notifications.email import (
        build_notifier_from_settings,
        send_test_email,
    )
    from job_hunter.web.deps import get_effective_settings

    s = get_effective_settings(request)

    if not s.notification_email:
        return JSONResponse(
            {"error": "Notification recipient email must be set."},
            status_code=400,
        )

    # Temporarily force enabled so build_notifier works for the test
    orig_enabled = s.notifications_enabled
    s.notifications_enabled = True
    try:
        notifier = build_notifier_from_settings(s)
    finally:
        s.notifications_enabled = orig_enabled

    if notifier is None:
        provider = s.email_provider
        if provider == "resend":
            return JSONResponse(
                {"error": "Resend API key is not configured."},
                status_code=400,
            )
        return JSONResponse(
            {"error": "SMTP host is not configured."},
            status_code=400,
        )

    ok = send_test_email(notifier)
    if ok:
        return {"sent": True}
    detail = getattr(notifier, "last_error", "") or "Failed to send. Check settings."
    return JSONResponse({"error": detail}, status_code=500)


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "not set" if not key else "****"
    return f"****{key[-4:]}"

