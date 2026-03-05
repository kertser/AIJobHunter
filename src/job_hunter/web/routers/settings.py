"""Settings router — view/edit runtime configuration."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["settings"])


class SettingsUpdate(BaseModel):
    mock: bool | None = None
    dry_run: bool | None = None
    headless: bool | None = None
    slowmo_ms: int | None = None
    log_level: str | None = None
    openai_api_key: str | None = None


@router.get("/settings")
async def settings_page(request: Request):
    templates = request.app.state.templates
    settings = request.app.state.settings
    return templates.TemplateResponse(request, "settings.html", {
        "settings": settings,
        "api_key_masked": _mask_key(settings.openai_api_key),
    })


@router.get("/api/settings")
async def get_settings_api(request: Request):
    s = request.app.state.settings
    return {
        "mock": s.mock,
        "dry_run": s.dry_run,
        "headless": s.headless,
        "slowmo_ms": s.slowmo_ms,
        "log_level": s.log_level.value,
        "data_dir": str(s.data_dir),
        "llm_provider": s.llm_provider,
        "openai_api_key": _mask_key(s.openai_api_key),
    }


@router.put("/api/settings")
async def update_settings_api(body: SettingsUpdate, request: Request):
    s = request.app.state.settings
    if body.mock is not None:
        s.mock = body.mock
    if body.dry_run is not None:
        s.dry_run = body.dry_run
    if body.headless is not None:
        s.headless = body.headless
    if body.slowmo_ms is not None:
        s.slowmo_ms = body.slowmo_ms
    if body.log_level is not None:
        from job_hunter.config.models import LogLevel
        s.log_level = LogLevel(body.log_level)
    if body.openai_api_key and body.openai_api_key.strip():
        s.openai_api_key = body.openai_api_key.strip()
    return {"updated": True}


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "not set" if not key else "****"
    return f"****{key[-4:]}"

